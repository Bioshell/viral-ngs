'''This gives a main() function that serves as a nice wrapper
around other commands and presents the ability to serve up multiple
command-line functions from a single python script.
'''

import os
import os.path
import tempfile
import textwrap
import sys
import shutil
import logging
import argparse
import functools
import inspect
import traceback

import util.version
import util.file
import util.misc
import util.cmd_plugins
import util.metadata

import pluggy

__author__ = "dpark@broadinstitute.org"
__version__ = util.version.get_version()

log = logging.getLogger()
tmp_dir = None


@util.cmd_plugins.cmd_hookimpl(trylast=True)
def cmd_call_cmd(cmd_main, args):
    return cmd_main(args)

@util.cmd_plugins.cmd_hookimpl(trylast=True)
def cmd_handle_file_arg(val):
    return val

class color(object):
    """ *nix terminal control characters for altering text display
    """
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    DARKCYAN = '\033[36m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

def setup_logger(log_level):
    loglevel = getattr(logging, log_level.upper(), None)
    assert loglevel, "unrecognized log level: %s" % log_level
    log.setLevel(loglevel)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s - %(module)s:%(lineno)d:%(funcName)s - %(levelname)s - %(message)s"))
    log.addHandler(h)


def script_name():
    return os.path.basename(sys.argv[0]).rsplit('.', 1)[0]


def common_args(parser, arglist=(('tmp_dir', None), ('loglevel', None))):
    for k, v in arglist:
        if k == 'loglevel':
            if not v:
                v = 'INFO'
            parser.add_argument("--loglevel",
                                dest="loglevel",
                                help="Verboseness of output.  [default: %(default)s]",
                                default=v,
                                choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'EXCEPTION'))
        elif k == 'tmp_dir':
            if not v:
                v = util.file.find_tmp_dir()
            parser.add_argument("--tmp_dir",
                                dest="tmp_dir",
                                help="Base directory for temp files. [default: %(default)s]",
                                default=v)
            parser.add_argument("--tmp_dirKeep",
                                action="store_true",
                                dest="tmp_dirKeep",
                                help="""Keep the tmp_dir if an exception occurs while
                    running. Default is to delete all temp files at
                    the end, even if there's a failure.""",
                                default=False)
        elif k == 'threads':
            if v is None:
                text_default = "all available cores"
            else:
                text_default = v
            parser.add_argument('--threads',
                                dest="threads",
                                type=int,
                                help="Number of threads (default: {})".format(text_default),
                                default=v)
        elif k == 'version':
            if not v:
                v = __version__
            parser.add_argument('--version', '-V', action='version', version=v)
        else:
            raise Exception("unrecognized argument %s" % k)
    return parser

def main_command(mainfunc):
    ''' This wraps a python method in another method that can be called
        with an argparse.Namespace object. When called, it will pass all
        the values of the object on as parameters to the function call.
    '''

    argspec = util.misc.getargspec(mainfunc)
    assert argspec[1:3] == (None, None), 'Command impls with *args or **kwargs not supported'
    mainfunc_args = set(util.misc.flatten(argspec.args))

    @util.misc.wraps(mainfunc)
    def _main(args):
        return mainfunc(**util.misc.dict_subset(vars(args), mainfunc_args))

    return _main

def attach_main(parser, cmd_main, split_args=False):
    ''' This attaches the main function call to a parser object.
    '''
    if split_args:
        cmd_main = main_command(cmd_main)

    load_cmd_plugins()

    util.cmd_plugins.cmd_plugin_mgr.hook.cmd_configure_parser(parser=parser)

    @util.misc.wraps(cmd_main)
    def call_main(args):
        return util.cmd_plugins.cmd_plugin_mgr.hook.cmd_call_cmd(cmd_main=cmd_main, args=args, config={})

    parser.description = cmd_main.__doc__
    parser.set_defaults(func_main=call_main)
    return parser

class _HelpAction(argparse._HelpAction):

    def __call__(self, parser, namespace, values, option_string=None):

        print("\nEnter a subcommand to view additional information:")

        # retrieve subparser actions from parser
        subparsers_actions = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)]

        indent_space = " " * 5
        for subparsers_action in subparsers_actions:
            # get all subparsers and print descriptions for each
            for choice, subparser in subparsers_action.choices.items():
                print("\n{indent}{filename} {cmd} [...]".format(indent=indent_space, filename=os.path.basename(sys.argv[0]), cmd=color.BOLD+choice+color.END))

                # if the subparser has a description string, format and print it
                if subparser.description:
                    # clean up line breaks and spaces in the triple-quoted string
                    help_description = subparser.description.replace("\n","").replace(".  ",". ").replace("  ","").replace("\t","")
                    help_description = help_description.strip()
                    # wrap text to a set width
                    help_description = textwrap.fill(help_description, 60)
                    # indent each line
                    help_description = help_description.replace("\n","\n{}".format(indent_space*2))
                    print("{}{}".format(indent_space*2, help_description))

        print()
        parser.print_help()

        parser.exit()


def make_parser(commands, description):
    ''' commands: a list of pairs containing the following:
            1. name of command (string, no whitespace)
            2. method to call (no arguments) that returns an argparse parser.
            If commands contains exactly one member and the name of the
            only command is None, then we get rid of the whole multi-command
            thing and just present the options for that one function.
        description: a long string to present as a description of your script
            as a whole if the script is run with no arguments
    '''
    if len(commands) == 1 and commands[0][0] == None:
        # only one (nameless) command in this script, simplify
        parser = commands[0][1]()
        parser.set_defaults(command='')
    else:
        # multiple commands available
        parser = argparse.ArgumentParser(description=description, usage='%(prog)s subcommand', add_help=False)
        parser.add_argument('--help', '-h', action=_HelpAction, help=argparse.SUPPRESS)
        parser.add_argument('--version', '-V', action='version', version=__version__, help=argparse.SUPPRESS)
        subparsers = parser.add_subparsers(title='subcommands', dest='command', metavar='\033[F') # \033[F moves cursor up
        for cmd_name, cmd_parser in commands:
            help_str = cmd_parser.__doc__ if cmd_parser.__doc__ and len(cmd_parser.__doc__) else None
            # give a blank string for help if the parser docstring is null
            # so sphinx-argparse doesnt't render "Undocumented"
            if (not help_str) and os.environ.get('READTHEDOCS') or 'sphinx' in sys.modules:
                help_str = "   "
            p = subparsers.add_parser(cmd_name, help=help_str)
            cmd_parser(p)
    return parser


def main_argparse(commands, description):
    parser = make_parser(commands, description)

    # if called with no arguments, print help
    if len(sys.argv) == 1:
        parser.parse_args(['--help'])
    elif len(sys.argv) == 2 and (len(commands) > 1 or commands[0][0] != None):
        parser.parse_args([sys.argv[1], '--help'])
    args = parser.parse_args()

    setup_logger(not hasattr(args, 'loglevel') and 'DEBUG' or args.loglevel)
    log.info("software version: %s, python version: %s", __version__, sys.version)
    log.info("command: %s %s %s", sys.argv[0], sys.argv[1],
             ' '.join(["%s=%s" % (k, v) for k, v in vars(args).items() if k not in ('command', 'func_main')]))

    if hasattr(args, 'tmp_dir'):
        # If this command has a tmp_dir option, use that as a base directory
        # and create a subdirectory within it which we will then destroy at
        # the end of execution.

        proposed_dir = 'tmp-%s-%s' % (script_name(), args.command)
        for e in ('LSB_JOBID', 'JOB_ID'): # LSB_JOBID is for LSF, JOB_ID is for UGER/GridEngine
            if e in os.environ:
                proposed_dir = 'tmp-%s-%s-%s-%s' % (script_name(), args.command, os.environ[e],
                                                    os.environ.get('LSB_JOBINDEX','0'))
                break
        tempfile.tempdir = tempfile.mkdtemp(prefix='%s-' % proposed_dir, dir=args.tmp_dir)
        log.debug("using tempDir: %s", tempfile.tempdir)
        os.environ['TMPDIR'] = tempfile.tempdir  # this is for running R
        try:
            ret = args.func_main(args)
        finally:
            if (hasattr(args, 'tmp_dirKeep') and args.tmp_dirKeep) or util.file.keep_tmp():
                log.debug(
                    "After running %s, saving tmp_dir at %s", args.command, tempfile.tempdir)
            else:
                shutil.rmtree(tempfile.tempdir)
    else:
        # otherwise just run the command
        ret = args.func_main(args)
    if ret is None:
        ret = 0
    return ret


class BadInputError(RuntimeError):

    '''Indicates that an invalid input was given to a command'''

    def __init__(self, reason):
        super(BadInputError, self).__init__(reason)

def check_input(condition, error_msg):
    '''Check input to a command'''
    if not condition:
        raise BadInputError(error_msg)

def run_cmd(module, cmd, args):
    """Run command after parsing its arguments with the command's parser.
    
    Args:
        module: the module object for the script containing the command
        cmd: the command name
        args: list of args to the command
    """
    if isinstance(module, str): module = sys.modules[module]
    parser_fn = dict(getattr(module, '__commands__'))[cmd]
    args_parsed = parser_fn(argparse.ArgumentParser()).parse_args(map(str, args))
    args_parsed.func_main(args_parsed)

def load_cmd_plugins():
    """Load plugins we will use."""

    if not util.cmd_plugins.cmd_plugin_mgr.list_name_plugin():

        util.cmd_plugins.cmd_plugin_mgr.register(sys.modules[__name__])
        util.cmd_plugins.cmd_plugin_mgr.register(util.metadata)
