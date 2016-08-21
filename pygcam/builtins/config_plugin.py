'''
.. codeauthor:: Richard Plevin

.. Copyright (c) 2016 Richard Plevin
   See the https://opensource.org/licenses/MIT for license details.
'''
from ..error import PygcamException, CommandlineError
from ..subcommand import SubcommandABC

class ConfigCommand(SubcommandABC):
    VERSION = '0.2'

    def __init__(self, subparsers):
        kwargs = {'help' : '''List the values of configuration variables from
                  ~/.pygcam.cfg configuration file.'''}

        super(ConfigCommand, self).__init__('config', subparsers, kwargs)

    def addArgs(self, parser):
        parser.add_argument('-d', '--useDefault', action='store_true',
                            help='''Indicates to operate on the DEFAULT
                                    section rather than the project section.''')

        parser.add_argument('-e', '--edit', action='store_true',
                            help='''Edit the configuration file. The command given by the
                            value of config variable GCAM.TextEditor is run with the
                            .pygcam.cfg file as an argument.''')

        parser.add_argument('name', nargs='?', default='',
                            help='''Show the names and values of all parameters whose
                            name contains the given value. The match is case-insensitive.
                            If not specified, all variable values are shown.''')

        parser.add_argument('-x', '--exact', action='store_true',
                            help='''Treat the text not as a substring to match, but
                            as the name of a specific variable. Match is case-sensitive.
                            Prints only the value.''')

        parser.add_argument('-t', '--test', action='store_true',
                            help='''Test the settings in the configuration file to ensure
                            that the basic setup is ok, i.e., required parameters have
                            values that make sense. If specified, no variables are displayed.''')

        parser.add_argument('--version', action='version', version='%(prog)s ' + self.VERSION)

        return parser

    def testConfig(self, section):
        import os
        from ..config import getParam

        requiredDirs = ['SandboxRoot', 'SandboxDir', 'ProjectRoot', 'ProjectDir',
                        'QueryDir', 'MI.Dir', 'RefWorkspace', 'TempDir']
        requiredFiles = ['ProjectXmlFile', 'RefConfigFile', 'MI.JarFile']
        optionalDirs  = ['UserTempDir']
        optionalFiles = ['RegionMapFile', 'RewriteSetsFile']

        dirVars  = requiredDirs  + optionalDirs
        fileVars = requiredFiles + optionalFiles

        optionalVars = optionalDirs + optionalFiles

        for item in dirVars + fileVars:
            var = 'GCAM.' + item
            value = getParam(var)

            if not value:
                if item in optionalVars:
                    continue
                print("Config variable %s is empty" % var)

            elif not os.path.lexists(value):
                print("Config variable %s refers to missing file or directory '%s'" % (var, value))

            elif not os.path.isfile(value) and item in fileVars:
                print("Config variable %s does not refer to a file (%s)" % (var, value))

            elif not os.path.isdir(value) and item in dirVars:
                print("Config variable %s does not refer to a directory (%s)" % (var, value))

            print 'OK:', var, '=', value

    def run(self, args, tool):
        import re
        import subprocess
        from ..config import getParam, _ConfigParser, USR_CONFIG_FILE

        if args.edit:
            cmd = "%s %s/%s" % (getParam('GCAM.TextEditor'), getParam('Home'), USR_CONFIG_FILE)
            print(cmd)
            exitStatus = subprocess.call(cmd, shell=True)
            if exitStatus != 0:
                raise PygcamException("TextEditor command '%s' exited with status %s\n" % (cmd, exitStatus))
            return

        section = 'DEFAULT' if args.useDefault else args.configSection

        if section != 'DEFAULT' and not _ConfigParser.has_section(section):
            raise CommandlineError("Unknown configuration file section '%s'" % section)

        if args.test:
            self.testConfig(section)
            return

        if args.name and args.exact:
            value = getParam(args.name, section=section, raiseError=False)
            if value is not None:
                print(value)
            return

        # if no name is given, the pattern matches all variables
        pattern = re.compile('.*' + args.name + '.*', re.IGNORECASE)

        print("[%s]" % section)
        for name, value in sorted(_ConfigParser.items(section)):
            if pattern.match(name):
                print("%22s = %s" % (name, value))


PluginClass = ConfigCommand
