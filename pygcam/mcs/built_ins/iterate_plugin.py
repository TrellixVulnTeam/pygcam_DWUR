# Copyright (c) 2016  Richard Plevin
# See the https://opensource.org/licenses/MIT for license details.

from pygcam.log import getLogger
from pygcam.subcommand import SubcommandABC

_logger = getLogger(__name__)

def driver(args, tool):
    """
    Run a command for each trialDir or expDir, using str.format to pass required args.
    Possible format args are: projectName, simId, trialNum, expName, simDir, trialDir, and expDir.
    """
    from subprocess import call

    from pygcam.config import getSection

    from ..Database import getDatabase
    from ..error import PygcamMcsUserError
    from ..context import Context
    from .. import util as U

    simId   = args.simId
    command = args.command
    expName = args.expName
    noRun   = args.noRun
    trialStr = args.trials

    projectName = getSection()

    if trialStr:
        trials = U.parseTrialString(trialStr)
    else:
        db = getDatabase()
        count = db.getTrialCount(simId)
        trials = xrange(count)

    # TBD: Add groupName
    context = Context(projectName=projectName, simId=simId, expName=expName)
    _logger.info('Running iterator for projectName=%s, simId=%d, expName=%s, trials=%s, command="%s"',
                 projectName, simId, expName, trialStr, command)

    # Create a dict to pass to str.format. These are constant across trials.
    argDict = {
        'projectName' : projectName,
        'simId'   : args.simId,
        'expName' : args.expName,
    }

    for trialNum in trials:
        argDict['trialNum'] = context.trialNum = trialNum
        argDict['expDir']   = context.getScenarioDir(create=True)
        argDict['trialDir'] = context.getTrialDir()
        argDict['simDir']   = context.getSimDir()

        try:
            cmd = command.format(**argDict)
        except Exception as e:
            raise PygcamMcsUserError("Bad command format: %s" % e)

        if noRun:
            print(cmd)
        else:
            try:
                call(cmd, shell=True)

            except Exception as e:
                raise PygcamMcsUserError("Failed to run command '%s': %s" % (cmd, e))


class IterateCommand(SubcommandABC):
    def __init__(self, subparsers):
        kwargs = {'help' : '''(MCS) Run a command in each trialDir, or if expName is given, 
        in each expDir. The following arguments are available for use in the command string,
        specified within curly braces: projectName, simId, trialNum, expName, trialDir, expDir.
        For example, to run the fictional program "foo" in each trialDir for a given set of
        parameters, you might write:
        gt iterate -s1 -c "foo -s{simId} -t{trialNum} -i{trialDir}/x -o{trialDir}/y/z.txt".'''}
        super(IterateCommand, self).__init__('iterate', subparsers, kwargs)

    def addArgs(self, parser):
        # Required arguments
        parser.add_argument('-s', '--simId',   type=int, required=True,
                            help='The id of the simulation')

        parser.add_argument('-c', '--command', type=str, required=True,
                            help='''A command string to execute for each trial. The following
                            arguments are available for use in the command string, specified
                            within curly braces: projectName, simId, trialNum, expName, trialDir, 
                            expDir.''')

        parser.add_argument('-e', '--expName', type=str, default="",
                            help='The name of the experiment')

        parser.add_argument('-n', '--noRun', action='store_true',
                            help="Show the commands that would be executed, but don't run them")

        parser.add_argument('-t', '--trials', type=str, default=None,
                             help='''Comma separated list of trial or ranges of trials to run. Ex: 1,4,6-10,3.
                             Defaults to running all trials for the given simulation.''')

        return parser   # for auto-doc generation


    def run(self, args, tool):
        driver(args, tool)