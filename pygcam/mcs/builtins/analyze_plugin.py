# Copyright (c) 2016  Richard Plevin
# See the https://opensource.org/licenses/MIT for license details.

from pygcam.log import getLogger
from pygcam.subcommand import SubcommandABC

_logger = getLogger(__name__)

def driver(args, tool):
    """
    Analyze MCS results
    """
    import os
    from ..analysis import analyzeSimulation
    from ..error import PygcamMcsUserError

    if args.timeseries:
        import pandas as pd
        from pygcam.config import getParam
        from ..Database import getDatabase
        from ..timeseriesPlot import plotTimeSeries, plotForcingSubplots
        from ..util import stripYearPrefix

        simId = args.simId
        expList = args.expName.split(',')
        resultName = args.resultName
        xlabel = 'Year' # args.xlabel or 'Year'
        ymin = args.ymin
        ymax = args.ymax

        # special purpose plot
        forcingPlot = args.forcingPlot

        if not (expList[0] and resultName):
            raise PygcamMcsUserError("expName and resultName must be specified")

        db = getDatabase()
        trialCount = db.getTrialCount(simId)

        plotDir  = getParam('MCS.PlotDir')
        plotType = getParam('MCS.PlotType')

        allResults = db.getTimeSeries(simId, resultName, expList) # , regionName)
        if not allResults:
            raise PygcamMcsUserError('No timeseries results for simId=%d, expList=%s, resultName=%s' \
                                     % (simId, expList, resultName))

        def computeFilename(expName):
            basename = "%s-s%d-%s.%s" % (resultName, simId, expName, plotType)
            filename = os.path.join(plotDir, 's%d' % simId, basename)
            return filename

        # massage the data into the format required by plotTimeSeries
        def createRecord(pair):
            obj, expName = pair
            d = obj.__dict__
            d['expName'] = expName
            return d

        records = [createRecord(pair) for pair in allResults]
        resultDF = pd.DataFrame.from_records(records, index='seriesId')
        units = resultDF.units.iloc[0]
        resultDF.drop(['units', '_sa_instance_state', 'outputId'], axis=1, inplace=True)

        # convert column names like 'y2020' to '2020'
        cols = map(stripYearPrefix, resultDF.columns)
        resultDF.columns = cols

        if forcingPlot:
            filename = computeFilename('combo')
            plotForcingSubplots(resultDF, filename=filename, ci=95, show_figure=False)
            return

        for expName in expList:
            # create a copy so we can drop expName column for melt
            df = resultDF.query("expName == '%s'" % expName).copy()
            _logger.debug("Found %d result records for exp %s, result %s" % (len(df), expName, resultName))

            df.drop(['expName'], axis=1, inplace=True)
            df = pd.melt(df, id_vars=['runId'], var_name='year')

            title = '%s for %s' % (resultName, expName)
            filename = computeFilename(expName)
            _logger.debug('Saving timeseries plot to %s' % filename)

            reg = "" # '-' + regionName if regionName else ""
            extra = "name=%s trials=%d/%d simId=%d scenario=%s%s" % \
                    (resultName, resultDF.shape[0], trialCount, simId, expName, reg)

            # TBD: generalize this with a lookup table or file
            if units == 'W/m^2':
                units = 'W m$^{-2}$'

            plotTimeSeries(df, 'year', 'runId', title=title, xlabel=xlabel, ylabel=units, ci=[95], # [50, 95],
                           text_label=None, legend_name=None, legend_labels=None,
                           ymin=ymin, ymax=ymax, filename=filename, show_figure=False, extra=extra)

        # if doing timeseries plot, none of the other options are relevant
        return

    if not (args.exportInputs or args.resultFile or args.plot or args.importance or
                args.groups or args.plotInputs or args.stats or args.convergence or
                args.exportEMA):
        msg = 'Must specify at least one of: --export, --resultFile, --plot, --importance, --groups, --distros, --stats, --convergence, --exportEMA'
        raise PygcamMcsUserError(msg)

    analyzeSimulation(args)


class AnalyzeCommand(SubcommandABC):
    def __init__(self, subparsers):
        kwargs = {'help' : '''(MCS) Analyze simulation results stored in the database for the given simulation.
            At least one of -c, -d, -i, -g, -p, -t (or the longname equivalent) must be specified.'''}
        super(AnalyzeCommand, self).__init__('analyze', subparsers, kwargs)

    def addArgs(self, parser):
        # Required argument
        parser.add_argument('-s', '--simId', type=int, required=True,
                            help='The id of the simulation')

        # Optional arguments
        parser.add_argument('-c', '--convergence', action='store_true', default=False,
                            help='Generate convergence plots for mean, std dev, skewness, and 95%% coverage interval.')

        parser.add_argument('-d', '--distros', dest='plotInputs', action='store_true', default=False,
                            help='Plot frequency distributions for input parameters.')

        parser.add_argument('-e', '--expName', type=str,
                            help='The name of the experiment or scenario to run.')

        parser.add_argument('--exportEMA', type=str, default=None,
                            help='''Export results to the given .tar.gz file in a format suitable for analysis
                            using the EMA Workbench. The -e (--expName) and -r (--resultName) flags can hold
                            comma-delimited lists of experiments and results, respectively.''')

        parser.add_argument('--forcingPlot', action='store_true',
                            help='''Plot the data in a good format for multiple forcing timeseries plots''')

        parser.add_argument('-g', '--groups', action='store_true',
                            help='Show the uncertainty importance for groups of parameters.')

        parser.add_argument('-i', '--importance', action='store_true', default=False,
                            help='Show the uncertainty importance for each parameter.')

        parser.add_argument('-l', '--limit', type=int, required=False, default=-1,
                            help='Limit the analysis to the given number of results')

        parser.add_argument('-m', '--min', type=float, required=False, default=None,
                            help='''Limit the analysis to values (for the result named with -r) greater
                            than or equal to this value''')

        parser.add_argument('-M', '--max', type=float, required=False, default=None,
                            help='''Limit the analysis to values (for the result named with -r) less
                            than or equal to this value''')

        parser.add_argument('-o', '--exportInputs', type=str, default=None,
                            help='A file into which to export input (trial) data.')

        parser.add_argument('-O', '--resultFile', type=str, default=None,
                            help='''Export all model results to the given file. When used with this option,
                            the -r (--resultName) and -e (--expName) flags can be comma-delimited lists of
                            result names and experiment names (scenarios), respectively. The output file,
                            in CSV format will have a header (and data in the form) "trialNum,value,expName,resultName"''')

        parser.add_argument('-p', '--plot', action='store_true', default=False,
                            help='''Plot a histogram of the frequency distribution for the named model output
                            (-r required).''')

        parser.add_argument('-R', '--regionName', default=None,
                            help='The region to plot timeseries results for')

        parser.add_argument('-r', '--resultName', type=str, default=None,
                            help='The name of the result variable to analyze.')

        parser.add_argument('-S', '--stats', action='store_true', default=False,
                            help='Print mean, median, max, min, std dev, skewness, and 95%% coverage interval.')

        parser.add_argument('-t', '--timeseries', action='store_true',
                            help='Plot a timeseries distribution')

        parser.add_argument('-x', '--xlabel', dest='xlabel', type=str, default=r'g CO$_2$e MJ$^{-1}$',
                            help='Specify a label for the x-axis in the histogram.')

        parser.add_argument('--ymax', type=float, default=None,
                            help='''Set the scale of a figure by indicating the value to show as the
                            maximum Y value. (By default, scale is set according to the data.)''')

        parser.add_argument('--ymin', type=float, default=None,
                            help='''Set the scale of a figure by indicating the value (given as abs(value),
                            but used as -value) to show as the minimum Y value''')

        return parser   # for auto-doc generation


    def run(self, args, tool):
        driver(args, tool)
