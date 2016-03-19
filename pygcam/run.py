'''
.. Created on: 2/26/15

.. codeauthor:: Rich Plevin <rich@plevin.com>

.. Copyright (c) 2016 Richard Plevin
   See the https://opensource.org/licenses/MIT for license details.
'''
import os
import subprocess
from lxml import etree as ET
from pygcam.common import mkdirs
from pygcam.log import getLogger
from pygcam.config import getConfig, getParam

_logger = getLogger(__name__)

def readScenarioName(configFile):
    """
    Read the file `configFile` and extract the scenario name.

    :param configFile: (str) the path to a GCAM configuration file
    :return: (str) the name of the scenario defined in `configFile`
    """
    parser = ET.XMLParser(remove_blank_text=True)
    tree   = ET.parse(configFile, parser)
    scenarioName = tree.find('//Strings/Value[@name="scenarioName"]')
    return scenarioName.text

def setupWorkspace(runWorkspace):
    refWorkspace = getParam('GCAM.RefWorkspace')

    if os.path.samefile(runWorkspace, refWorkspace):
        _logger.info("setupWorkspace: run workspace is reference workspace; no setup performed")
        return

    def workspaceSymlink(src):
        'Create a link in the new workspace to the equivalent file in the main GCAM workspace'
        dstPath = os.path.join(runWorkspace, src)
        if not os.path.lexists(dstPath):
            dirName = os.path.dirname(dstPath)
            mkdirs(dirName)
            srcPath = os.path.join(refWorkspace, src)
            os.symlink(srcPath, dstPath)

    # Create the workspace if needed
    if not os.path.isdir(runWorkspace):
        _logger.info("Creating GCAM workspace '%s'", runWorkspace)

    # Create a local output dir
    outDir = os.path.join(runWorkspace, 'output')
    mkdirs(outDir)

    logPath = os.path.join(runWorkspace, 'exe', 'logs')
    mkdirs(logPath)

    # Create link in the new workspace "exe" dir to the executable program and other required files/dirs
    exePath = os.path.join('exe', getParam('GCAM.Executable'))      # expressed as relative to the exe dir
    workspaceSymlink(exePath)
    workspaceSymlink(os.path.join('exe', 'configuration.xml'))      # link to default configuration file
    workspaceSymlink(os.path.join('exe', 'log_conf.xml'))           # and log configuration file
    workspaceSymlink('input')
    workspaceSymlink('libs')

    # For basex
    if os.path.lexists(os.path.join(refWorkspace, 'exe', 'WriteLocalBaseXDB.class')):
        workspaceSymlink(os.path.join('exe', 'WriteLocalBaseXDB.class'))

    # Add symlinks to dirs holding files generated by "setup" scripts
    def xmlLink(varName, xmlDir):
        src = getParam(varName)
        dst = os.path.join(runWorkspace, xmlDir)

        if os.path.lexists(dst):
            os.remove(dst)

        os.symlink(src, dst)

    # TBD: decide whether these are needed. Should be up to the user
    # whether their Main_User_Workspace has links to these directories.
    # xmlLink('GCAM.LocalXML', 'local-xml')
    # xmlLink('GCAM.DynXML',   'dyn-xml')


CONFIG_FILE_DELIM = ':'

def driver(args):
    getConfig(args.configSection)
    from .config import CONFIG_VAR_NAME, WORKSPACE_VAR_NAME, NO_RUN_GCAM_VAR_NAME

    isQueued = (CONFIG_VAR_NAME in os.environ)     # see if this is a batch run on cluster

    if isQueued:
        configFiles = os.environ[CONFIG_VAR_NAME].split(CONFIG_FILE_DELIM)
        workspace   = os.environ[WORKSPACE_VAR_NAME]
        args.noRunGCAM = int(os.environ[NO_RUN_GCAM_VAR_NAME])
        runQsub = False
    else:
        scenarios  = args.scenario.split(',') if args.scenario else None
        runLocal   = args.runLocal
        runQsub    = not runLocal
        jobName    = args.jobName    # args default is "queueGCAM"
        queueName  = args.queueName  or getParam('GCAM.DefaultQueue')
        workspace  = args.workspace  or getParam('GCAM.RunWorkspaceRoot')
        workspace  = os.path.abspath(os.path.expanduser(workspace))     # handle ~ in pathname
        setupWorkspace(workspace)

    # less confusing names
    showCommandsOnly = args.noRun
    postProcessOnly  = args.noRunGCAM

    # Optional script to run after successful GCAM runs
    postProcessor = not args.noPostProcessor and (args.postProcessor or getParam('GCAM.PostProcessor'))

    exeDir = os.path.join(workspace, 'exe')

    if not isQueued:
        if scenarios:
            # Translate scenario names into config file paths, assuming scenario FOO lives in
            # {scenariosDir}/FOO/config.xml
            scenariosDir = os.path.abspath(args.scenariosDir or getParam('GCAM.ScenariosDir') or '.')
            configFiles  = map(lambda name: os.path.join(scenariosDir, name, "config.xml"), scenarios)
        else:
            configFiles = map(os.path.abspath, args.configFile.split(',')) \
                            if args.configFile else [os.path.join(exeDir, 'configuration.xml')]

    if runQsub:
        logFile  = os.path.join(exeDir, 'queueGCAM.log')
        minutes  = (args.minutes or float(getParam('GCAM.Minutes'))) * len(configFiles)
        walltime = "%02d:%02d:00" % (minutes / 60, minutes % 60)
        configs  = CONFIG_FILE_DELIM.join(configFiles)

        # This dictionary is applied to the string value of GCAM.BatchCommand, via
        # the str.format method, which must specify options using any of the keys.
        batchArgs = {'logFile'   : logFile,
                     'minutes'   : minutes,
                     'walltime'  : walltime,
                     'queueName' : queueName,
                     'jobName'   : jobName,
                     'configs'   : configs,
                     'exeDir'    : exeDir,
                     'workspace' : workspace,
                     'noRunGCAM' : int(args.noRunGCAM)}

        batchCmd = getParam('GCAM.BatchCommand')
        scriptPath = os.path.abspath(__file__)
        batchCmd += ' ' + scriptPath

        try:
            command = batchCmd.format(**batchArgs)
            print command
        except KeyError as e:
            print 'Badly formatted batch command string in config file: %s.\nValid keywords are %s' % (e, batchArgs.keys())
            exit -1

        if not showCommandsOnly:
            exitStatus = subprocess.call(command, shell=True)
            return exitStatus

    else:
        # Run locally, which might mean on a desktop machine, interactively on a
        # compute node (via "qsub -I", or in batch mode on a compute node.
        gcamPath = getParam('GCAM.Executable')
        print "cd", exeDir
        os.chdir(exeDir)        # if isQsubbed, this is redundant but harmless

        exitStatus = 0

        for configFile in configFiles:
            gcamArgs = [gcamPath, '-C%s' % configFile]  # N.B. GCAM doesn't allow space between -C and filename
            gcamCmd   = ' '.join(gcamArgs)

            if postProcessor:
                scenarioName = readScenarioName(configFile)
                postProcCmd  = ' '.join([postProcessor, workspace, configFile, scenarioName])

            if showCommandsOnly:
                print gcamCmd
                print postProcCmd if postProcessor else "No post-processor defined"
                continue

            if not postProcessOnly:
                print gcamCmd
                exitStatus = subprocess.call(gcamArgs, shell=False)
                if exitStatus <> 0:
                    print "GCAM failed with command: %s" % gcamCmd
                    return exitStatus

            if postProcessor:
                print postProcCmd
                exitStatus = subprocess.call(postProcArgs, shell=False)
                if exitStatus <> 0:
                    print "Post-processor '%s' failed for workspace '%s', configuration file '%s, scenario '%s'" % \
                          (postProcessor, workspace, configFile, scenarioName)
                    return exitStatus

        return exitStatus
