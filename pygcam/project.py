#!/usr/bin/env python
"""
.. Support for running a sequence of operations for a GCAM project
   that is described in an XML file.

.. Copyright (c) 2015 Richard Plevin
   See the https://opensource.org/licenses/MIT for license details.
"""
from __future__ import print_function
from copy import copy
import glob
import os
import re
import shlex
import sys
from os.path import join

from lxml import etree as ET

from .config import getParam, setParam, getConfigDict
from .constants import LOCAL_XML_NAME, XML_SRC_NAME
from .error import PygcamException, CommandlineError, FileFormatError
from .log import getLogger, getLogLevel
from .utils import (getTempFile, flatten, shellCommand, getBooleanXML, unixPath,
                    simpleFormat, resourceStream, QueryResultsDir, XMLFile)
from .xmlSetup import ScenarioSetup

__version__ = '0.2'

_logger = getLogger(__name__)

DefaultProjectFile = './project.xml'

def dropArgs(args, shortArg, longArg, takesArgs=True):
    args = copy(args)

    # Delete separated versions, e.g., "-s foo" and "--scenario foo"
    for arg in [shortArg, longArg]:
        while arg in args:
            argIndex = args.index(arg)
            if takesArgs:
                del args[argIndex + 1]
            del args[argIndex]

    if takesArgs:
        # Delete contiguous versions, e.g., "-sfoo" and "--scenario=foo"
        matches = filter(lambda s: s.startswith(shortArg) or s.startswith(longArg + '='), args)
        for arg in matches:
            while arg in args:   # in case an arg is repeated...
                args.remove(arg)

    return args

def decacheVariables():
    SimpleVariable.decache()
    _TmpFileBase.decache()

class _TmpFileBase(object):
    """
    Defines features common to _TmpFile and Queries.
    """
    Instances = {}

    def __init__(self, node):
        self.varName = node.get('varName')
        self.delete  = getBooleanXML(node.get('delete',  '1'))

    # Methods to allow subclasses to use superclass' Instances dict.
    # N.B. can't use cls.Instances or each subclass gets own dict.
    @classmethod
    def setInstance(cls, key, value):
        _TmpFileBase.Instances[key] = value

    @classmethod
    def getInstance(cls, key):
        return cls.Instances.get(key)

    @classmethod
    def decache(cls):
        _TmpFileBase.Instances = {}

    @classmethod
    def writeFiles(cls, argDict):
        """
        Write the files and set the associated variables to the generated filenames.
        """
        for obj in cls.Instances.values():
            path = obj.write(argDict)
            argDict[obj.varName] = path

    def write(self, argDict):
        # subclass responsibility
        pass

class _TmpFile(_TmpFileBase):
    """
    Represents the ``<tmpFile>`` element in the projects.xml file.
    """
    def __init__(self, node):
        """
        defaults is an optional _TmpFile instance from which to
        take default file contents, which are appended to or
        replaced by the list defined here.
        """
        super(_TmpFile, self).__init__(node)

        self.replace = getBooleanXML(node.get('replace', '0'))
        self.eval    = getBooleanXML(node.get('eval',    '1'))    # convert {args} before writing file
        self.dir     = node.get('dir')
        self.path    = None

        name = self.varName
        default = self.getInstance(name)  # save default node of the same name, if any
        self.setInstance(name, self)      # replace default with our own definition

        textNodes = node.findall('text')
        defaults = []

        # Substitute any lines based on matching tags, unless we're replacing
        # all the defaults or there are no defaults to replace.
        if not self.replace and default:
            def getTag(node):
                return node.get('tag')

            tags = map(getTag, filter(getTag, textNodes))

            # Save all the defaults that are not overridden by current definitions
            defaults = filter(lambda node: node.get('tag') not in tags, default.textNodes)

        self.textNodes = defaults + textNodes

    def write(self, argDict):
        # Note: TempFiles are deleted in the main driver (tool.py)
        path = getTempFile(suffix='.project.txt', tmpDir=self.dir, delete=self.delete)
        path = unixPath(path)

        with open(path, 'w') as f:
            text = '\n'.join(map(lambda x: x.text or '', self.textNodes)) + '\n'
            if text and self.eval:
                text = simpleFormat(text, argDict)
            f.write(text)

        self.path = path
        return path

class Queries(_TmpFileBase):
    """
    Represents the ``<queries>`` element in the projects.xml file. We don't process the
    <queries> element here; we just store it so we can write it to a temp file as needed.
    Actual reading/processing of contents is handled in queryFile.py.
    """
    def __init__(self, node):
        super(Queries, self).__init__(node)
        self.tree = ET.ElementTree(node)
        self.setInstance(self.varName, self)      # replace default with our own definition

    def write(self, _argDict):
        # Note: TempFiles are deleted in the main driver (tool.py)
        path = getTempFile(suffix='.queries.xml', delete=self.delete)
        path = unixPath(path)
        self.path = path

        self.tree.write(path, xml_declaration=True, pretty_print=True)
        return path

# deprecated
# class ScenarioGroup(object):
#     """
#     Represents the ``<scenarioGroup>`` element in the projects.xml file.
#     """
#     def __init__(self, node):
#         self.name = node.get('name')
#         self.isDefault   = getBooleanXML(node.get('default', default='0'))
#         self.useGroupDir = getBooleanXML(node.get('useGroupDir', default='0'))
#
#         scenarioNodes = node.findall('scenario')
#         scenarios = map(Scenario, scenarioNodes)
#
#         self.scenarioDict = {scen.name : scen for scen in scenarios}
#
#         baselineNode = getBaseline(scenarios)
#         self.baseline = baselineNode.name
#
# class Scenario(object):
#     """
#     Represents the ``<scenario>`` element in the projects.xml file.
#     """
#     def __init__(self, node):
#         self.name = node.get('name')
#         self.isActive   = getBooleanXML(node.get('active',   default='1'))
#         self.isBaseline = getBooleanXML(node.get('baseline', default='0'))
#         self.subdir = node.get('subdir', default=self.name)


class Step(object):
    maxStep = 0        # for auto-numbering steps lacking a sequence number

    """
    Represents the ``<step>`` element in the projects.xml file.
    """
    def __init__(self, node):
        self.seq     = int(node.get('seq', 0))
        self.name    = node.get('name')
        self.runFor  = node.get('runFor', 'all')
        self.group   = node.get('group', None)
        self.command = node.text

        if not self.command:
            raise FileFormatError("<step name='%s'> is missing command text" % self.name)

        if self.seq:
            Step.maxStep = max(self.seq, self.maxStep)
        else:
            Step.maxStep += 1
            self.seq = Step.maxStep

    def __str__(self):
        return "<Step name='%s' seq='%s' runFor='%s'>%s</Step>" % \
               (self.name, self.seq, self.runFor, self.command)

    def run(self, project, baseline, scenario, argDict, tool, noRun=False):
        runFor = self.runFor
        isBaseline = (baseline == scenario.name)
        isPolicy = not isBaseline

        # See if this step should be run.
        if runFor != 'all' and ((isBaseline and runFor != 'baseline') or (isPolicy and runFor != 'policy')):
            return

        # User can substitute an empty command to delete a default step
        if not self.command:
            return

        try:
            command = simpleFormat(self.command, argDict)    # replace vars in template
        except KeyError as e:
            raise FileFormatError("%s -- No such variable exists in the project XML file" % e)

        _logger.info("[%s, %s, %s] %s", scenario.name, self.seq, self.name, command)

        if not noRun:
            if command[0] == '@':       # run internally in gt
                argList = shlex.split(command[1:])
                argList = flatten(map(lambda s: glob.glob(s) or [s], argList))  # expand shell wildcards
                tool.run(argList=argList)
            else:
                shellCommand(command, shell=True)   # shell=True to expand shell wildcards and so on

class SimpleVariable(object):
    """
    Simple variable of name and value to allow loading config file
    variables alongside variables defined in the project XML file.
    """
    Instances = {}

    def __init__(self, name, value, evaluate=False):
        self.name      = name
        self.value     = value
        self.eval      = evaluate
        self.Instances[name] = self

    def getValue(self):
        return self.value

    def setValue(self, value):
        self.value = value

    def evaluate(self, argDict):
        return self.getValue()

    @classmethod
    def decache(cls):
        cls.Instances = {}

    @classmethod
    def instances(cls):
        return cls.Instances.values()

    @classmethod
    def definedVars(cls):
        return cls.Instances.keys()


    @classmethod
    def setFromDefault(cls, varName, defaultName):
        vars = cls.Instances
        if not varName in vars:
            vars[varName] = vars[defaultName]

    @classmethod
    def getDict(cls):
        argDict = {name: var.value for name, var in cls.Instances.items()}
        return argDict


class Variable(SimpleVariable):
    """
    Represents the ``<var>`` element in the projects.xml file.
    """
    def __init__(self, node):
        name     = node.get('name')
        evaluate = getBooleanXML(node.get('eval', 0))

        super(Variable, self).__init__(name, node.text, evaluate=evaluate)

    @classmethod
    def evaluateVars(cls, argDict):
        '''Evaluate vars and store results in argDict'''
        for name, var in cls.Instances.items():
            argDict[name] = var.evaluate(argDict)

    def evaluate(self, argDict, value=None):
        # optional arg lets us call it recursively
        value = value or self.getValue()

        # result = value.format(**argDict) if value and self.eval else value
        result = simpleFormat(value, argDict) if value and self.eval else value

        # recurse in case there are vars whose values are variable references
        return self.evaluate(argDict, value=result) if re.search('\{[^\}]+\}', result) else result

class Project(XMLFile):
    """
    Represents the ``<project>`` element in the projects.xml file.
    """
    def __init__(self, xmlFile, projectName, groupName=None):

        schemaStream = resourceStream('etc/project-schema.xsd')

        super(Project, self).__init__(xmlFile, schemaFile=schemaStream)

        self.projectName = projectName
        tree = self.tree
        projectNodes = tree.findall('project[@name="%s"]' % projectName)

        if len(projectNodes) == 0:
            raise FileFormatError("Project '%s' is not defined" % projectName)

        if len(projectNodes) > 1:
            raise FileFormatError("Project '%s' is defined %d times" % (projectName, len(projectNodes)))

        projectNode = projectNodes[0]

        self.subdir = projectNode.get('subdir', '')

        defaultsNode = tree.find('defaults')   # returns 1st match
        hasDefaults = defaultsNode is not None

        # Read referenced scenarios.xml file and add it as a child of projectNode
        nodes = projectNode.findall('scenariosFile')
        if len(nodes) != 1:
            raise FileFormatError("%s: <project> must define exactly one <scenariosFile> element" % xmlFile)
        filename = nodes[0].get('name')
        setupFile = os.path.join(os.path.dirname(xmlFile), filename)    # interpret as relative to including file
        self.scenarioSetup = ScenarioSetup.parse(setupFile)

        filename = getParam('GCAM.ScenarioSetupOutputFile')
        if filename:
            _logger.debug('Writing "%s"', filename)
            with open(filename, 'w') as stream:
                self.scenarioSetup.writeXML(stream)

        # deprecated (old approach)
        # self.scenarioGroups = scenarioGroups = map(ScenarioGroup, projectNode.findall('scenarios/scenarioGroup'))
        # self.scenarioGroupDict = {group.name : group for group in scenarioGroups}
        # TBD: new approach
        self.scenarioGroupDict = self.scenarioSetup.groupDict

        self.setGroup(groupName)    # if None, default group is set

        dfltSteps = map(Step, defaultsNode.findall('./steps/step')) if hasDefaults else []
        projSteps = map(Step, projectNode.findall('./steps/step'))
        allSteps  = dfltSteps + projSteps

        # project steps with same name and seq overwrite defaults
        self.stepsDict = stepsDict = {}
        for step in allSteps:
            key = "%s-%d" % (step.name, step.seq)
            stepsDict[key] = step

        self.vars = {}

        if hasDefaults:
            map(Variable, defaultsNode.findall('./vars/var'))

        map(Variable,  projectNode.findall('./vars/var'))

        dfltQueriesNodes = defaultsNode.findall('queries') if hasDefaults else []
        projQueriesNodes = projectNode.findall('queries')
        self.queryFiles  = map(Queries, dfltQueriesNodes + projQueriesNodes)

        dfltTmpFileNodes = defaultsNode.findall('tmpFile') if hasDefaults else []
        projTmpFileNodes = projectNode.findall('tmpFile')
        self.tmpFiles = map(_TmpFile, dfltTmpFileNodes + projTmpFileNodes)

    @staticmethod
    def validateXML(doc, raiseError=True):
        '''
        Validate a parsed project.xml file
        '''
        schemaStream = resourceStream('etc/project-schema.xsd')

        schemaDoc = ET.parse(schemaStream)
        schema = ET.XMLSchema(schemaDoc)

        if raiseError:
            schema.assertValid(doc)
        else:
            return schema.validate(doc)

    def setGroup(self, groupName=None):
        groupDict = self.scenarioGroupDict

        groupName = groupName or self.scenarioSetup.defaultGroup

        if groupName not in groupDict:
            raise FileFormatError("Group '%s' is not defined for project '%s'" % (groupName, self.projectName))

        self.scenarioGroupName = groupName
        self.scenarioGroup = scenarioGroup = groupDict[groupName]
        self.baselineName  = scenarioGroup.baseline
        self.scenarioDict  = scenarioGroup.finalDict

        return self.scenarioGroup

    def maybeListProjectArgs(self, args, knownGroups, knownScenarios, knownSteps):
        '''
        If user asked to list scenarios, steps, or variables, do so and quit.
        '''
        self.quit = False

        def showList(strings, header):
            self.quit = True
            if header:
                print(header)
            for s in strings:
                print('  ' + s)

        if args.listGroups:
            showList(knownGroups, 'Scenario groups:')

        if args.listScenarios:
            showList(knownScenarios, 'Scenarios:')

        if args.listSteps:
            showList(knownSteps, 'Steps:')

        if args.vars:
            varList = ["%15s = %s" % (name, value) for name, value in sorted(self.argDict.items())]
            showList(varList, 'Vars:')

        if self.quit:
            sys.exit(0)

    def getKnownSteps(self):
        '''
        Return a list of known steps in seq order, without duplicates.
        '''
        tmpDict = {}    # used to eliminate duplicates

        def uniqStep(step):
            key = step.name
            if tmpDict.get(key):
                return False # already in dict

            tmpDict[key] = 1
            return key       # first time for this name

        self.sortedSteps = sortedSteps = sorted(self.stepsDict.values(), key=lambda node: node.seq)
        knownSteps = [step.name for step in sortedSteps if uniqStep(step)]
        return knownSteps

    def getKnownScenarios(self):
        '''
        Return a list of known scenarios for the current project and scenarioGroup, alpha sorted
        '''
        # sorting by not(node.isBaseline) results in baseline preceding scenarios
        sortedScenarios = sorted(self.scenarioDict.values(), key=lambda node: not node.isBaseline)
        knownScenarios  = map(lambda node: node.name, sortedScenarios)
        return knownScenarios

    def getKnownGroups(self):
        '''
        Return a list of known scenarioGroups for the current project.
        '''
        sortedGroups = sorted(self.scenarioGroupDict.values(), key=lambda node: node.name)
        knownGroups = map(lambda node: node.name, sortedGroups)
        return knownGroups

    def validateProjectArgs(self, userArgs, knownArgs, argName):
        """
        If the user requested steps or scenarios that are not defined, raise an error.

        :param userArgs: a list of the elements (projects, groups, scenarios, steps)
          passed by the user on the command-line.
        :param knownArgs: a list of known elements of the given type
        :param argName: the tag of the XML element
        :return: nothing
        :raises: CommandlineError if the elements requested by the user are not defined in
          the current (project, scenario) context
        """
        unknownArgs = set(userArgs) - set(knownArgs)
        if unknownArgs:
            s = ' '.join(unknownArgs)
            raise CommandlineError("Requested %s do not exist in project '%s', group '%s': %s" % \
                                  (argName, self.projectName, self.scenarioGroupName, s))

    def sortScenarios(self, scenarioSet):
        """
        If a baseline is in the scenario set, move it to the front and return the new list
        """
        scenarios = list(scenarioSet)

        for scenarioName in scenarios:
            scenario = self.scenarioDict[scenarioName]
            if scenario.isBaseline:
                scenarios.remove(scenarioName)
                scenarios.insert(0, scenarioName)
                break

        return scenarios

    def run(self, scenarios, skipScenarios, steps, skipSteps, args, tool):
        """
        Command templates can include keywords curly braces that are substituted
        to create the command to execute in the shell. Variables are defined in
        the <vars> section of the project XML file.
        """
        projectName = self.projectName
        scenarioGroupName = self.scenarioGroupName
        groupDir = scenarioGroupName if self.scenarioGroup.useGroupDir else ''

        # Push the groupName back into config system so vars can use it
        setParam('GCAM.ScenarioGroup', groupDir, section=projectName)
        #setParam('GCAM.ScenarioGroup', scenarioGroupName, section=projectName)

        # Get the text values for all config variables, allowing variables
        # defined in the project to override them.
        cfgDict = getConfigDict(section=projectName)
        for name, value in cfgDict.items():
            SimpleVariable(name, value)

        self.argDict = argDict = Variable.getDict()

        # Add standard variables for use in step command substitutions
        argDict['project']       = projectName
        argDict['projectSubdir'] = subdir = self.subdir
        argDict['baseline']      = argDict['reference'] = baseline = self.baselineName     # baseline is synonym for reference
        argDict['scenarioGroup'] = scenarioGroupName
        argDict['projectSrcDir'] = unixPath(join('..', XML_SRC_NAME,   groupDir, subdir), rmFinalSlash=True)
        argDict['projectXmlDir'] = unixPath(join('..', LOCAL_XML_NAME, groupDir, subdir), rmFinalSlash=True)

        argDict['SEP']  = os.path.sep       # '/' on Unix; '\\' on Windows
        argDict['PSEP'] = os.path.pathsep   # ':' on Unix; ';' on Windows

        knownGroups    = self.getKnownGroups()
        knownScenarios = self.getKnownScenarios()
        knownSteps     = self.getKnownSteps()

        self.maybeListProjectArgs(args, knownGroups, knownScenarios, knownSteps)

        # Set steps / scenarios to all known values if user doesn't specify any
        steps = set(steps or knownSteps) - set(skipSteps or [])
        scenarios = set(scenarios or knownScenarios) - set(skipScenarios or [])

        # Check that the requested scenarios and steps are defined
        self.validateProjectArgs(scenarios, knownScenarios, 'scenarios')
        self.validateProjectArgs(steps,     knownSteps,     'steps')

        quitProgram = not args.noQuit

        scenarios = self.sortScenarios(scenarios)
        jobId = None

        sandboxDir = args.sandboxDir or argDict['GCAM.SandboxDir']

        # Delete all variants of scenario specification from shellArgs
        # so we can queue these one at a time.
        shellArgs = dropArgs(tool.shellArgs, '-S', '--scenario')
        shellArgs = dropArgs(shellArgs, '-D', '--distribute', takesArgs=False)

        for scenarioName in scenarios:
            scenario = self.scenarioDict[scenarioName]

            if not scenario.isActive:
                _logger.debug("Skipping inactive scenario: %s", scenarioName)
                continue

            # Construct gt command that does this scenario's steps
            # setting the -S flag for one scenario at a time.
            if args.distribute:
                scenarioArg = ['-S', scenarioName]
                scriptFile = tool.writeBatchScript(shellArgs + scenarioArg, delete=True)

                if scenario.isBaseline:
                    jobId = tool.runBatch2(scriptFile, jobName=args.jobName, queueName=args.queueName,
                                           logFile=args.logFile, minutes=args.minutes)
                else:
                    tool.runBatch2(scriptFile, jobName=args.jobName, queueName=args.queueName,
                                   logFile=args.logFile, minutes=args.minutes, dependsOn=jobId)
                continue

            # These get reset as each scenario is processed
            argDict['scenario']       = scenarioName
            argDict['scenarioSubdir'] = scenario.subdir or scenarioName
            argDict['sandboxDir']     = sandboxDir
            argDict['scenarioDir']    = scenarioDir = unixPath(join(sandboxDir, scenarioName))
            argDict['diffsDir']       = unixPath(join(scenarioDir, 'diffs'))
            argDict['batchDir']       = unixPath(join(scenarioDir, QueryResultsDir))     # used to be batch-{scenario}

            # set in case it wasn't already
            setParam('GCAM.SandboxDir', sandboxDir, section=projectName)

            # Evaluate dynamic variables and re-generate temporary files, saving paths in
            # variables indicated in <tmpFile> or <queries> elements. This is in the scenario
            # loop so run-time variables are handled correctly, though it does result in the
            # files being written multiple times (though with different values.)
            Variable.evaluateVars(argDict)
            _TmpFileBase.writeFiles(argDict)

            try:
                # Loop over all steps and run those that user has requested
                for step in self.sortedSteps:
                    if step.name in steps and (not step.group or step.group == scenarioGroupName):
                        argDict['step'] = step.name
                        step.run(self, baseline, scenario, argDict, tool, noRun=args.noRun)
            except PygcamException as e:
                if quitProgram:
                    raise
                _logger.error("Error running step '%s': %s", step.name, e)


    def dump(self, steps, scenarios):
        print("Scenario group:", self.scenarioGroupName)
        print("Requested steps:", steps)
        print("Requested scenarios:", scenarios)
        print("Defined steps:", self.getKnownSteps())
        print("Defined scenarios:", self.getKnownScenarios())
        print('Defined vars:')
        for name, var in Variable.Instances.items():
            print("  %15s : %s" % (name, var.getValue()))
        print('\nTmpFiles:')
        for t in self.tmpFiles:
            print("  " + t.varName)


def projectMain(args, tool):
    if not args.project:
        args.project = args.configSection or getParam('GCAM.DefaultProject')

    if not args.project:
        raise CommandlineError("run: must specify project name")

    def listify(items):
        '''Convert a list of comma-delimited strings to a single list of strings'''
        return flatten(map(lambda s: s.split(','), items)) if items else None

    steps     = listify(args.steps)
    skipSteps = listify(args.skipSteps)
    scenarios = listify(args.scenarios)
    skipScens = listify(args.skipScenarios)

    projectFile = args.projectFile or getParam('GCAM.ProjectXmlFile') or DefaultProjectFile

    project = Project(projectFile, args.project, args.group)

    groups = project.getKnownGroups() if args.allGroups else [args.group]

    for group in groups:
        project.setGroup(group)
        project.run(scenarios, skipScens, steps, skipSteps, args, tool)
