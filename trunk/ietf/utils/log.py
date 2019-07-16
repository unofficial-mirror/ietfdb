# Copyright The IETF Trust 2007-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import sys
import logging
import inspect
import os.path
import six
import traceback

try:
    import syslog
    logfunc = syslog.syslog
except ImportError:                     # import syslog will fail on Windows boxes
    logging.basicConfig(filename='tracker.log',level=logging.INFO)
    logfunc = logging.info
    pass

from django.conf import settings

import debug                            # pyflakes:ignore

def getclass(frame):
    cls = None
    argnames, varargs, varkw, defaults  = inspect.getargvalues(frame)
    if len(argnames) > 0:
        selfname = argnames[0]
        cls = defaults[selfname].__class__
    return cls

def getcaller():
    parent, pfile, pline, pfunction, lines, index = inspect.stack()[2]
    pmodule = inspect.getmodulename(pfile)
    pclass = getclass(parent)
    return (pmodule, pclass, pfunction, pfile, pline)

def log(msg, e=None):
    "Uses syslog by preference.  Logs the given calling point and message."
    global logfunc
    def _flushfunc():
        pass
    _logfunc = logfunc
    if settings.SERVER_MODE == 'test':
## Comment in when debugging for instance test smtp server failures:
#        if e:
#            _logfunc = debug.say
#            _flushfunc = sys.stdout.flush   # pyflakes:ignore (intentional redefinition)
#        else:
            return
    elif settings.DEBUG == True:
        _logfunc = debug.say
        _flushfunc = sys.stdout.flush   # pyflakes:ignore (intentional redefinition)
    if isinstance(msg, six.text_type):
        msg = msg.encode('unicode_escape')
    try:
        mod, cls, func, file, line = getcaller()
        file = os.path.abspath(file)
        file = file.replace(settings.BASE_DIR, "")
        if func == "<module>":
            where = ""
        else:
            where = " in " + func + "()"
    except IndexError:
        file, line, where = "/<UNKNOWN>", 0, ""
    _flushfunc()
    _logfunc("ietf%s(%d)%s: %s" % (file, line, where, msg))

logger = logging.getLogger('django')



def exc_parts():
    info = sys.exc_info()
    extype = info[0]
    value = info[1]
    tb = traceback.format_tb(info[2])
    return (extype, value, tb)

def build_traceback(stack):
    """
    Build something that looks sufficiently like a traceback to be passed to a
    logging.logger as the exc_info argument.
    """
    class Traceback():
        pass
    next = None
    for frame_record in stack:
        fr_frame, fr_filename, fr_lineno, fr_funcname, fr_context, fr_context_this = frame_record
        tb = Traceback()
        tb.tb_frame = fr_frame
        tb.tb_lasti = fr_frame.f_lasti
        tb.tb_lineno = fr_lineno
        tb.tb_next = next
        next = tb
        # Stop traceback at _get_response() -- we don't want to see the
        # middleware, debug server, or wsgi internals when the exception
        # occurs in our app code, below _get_response():
        if fr_funcname == '_get_response' and fr_filename.endswith('django/core/handlers/base.py'):
            break
    return tb

def assertion(statement, state=True):
    """
    This acts like an assertion.  It uses the django logger in order to send
    the failed assertion and a backtrace as for an internal server error.

    """
    stack = inspect.stack()[1:]
    frame = stack[0][0]
    value = eval(statement, frame.f_globals, frame.f_locals)
    if bool(value) != bool(state):
        settings.DEBUG = False
        if settings.DEBUG is True or settings.SERVER_MODE == 'test' :
            raise AssertionError("Assertion failed: '%s': %s != %s." % (statement, repr(value), state))
        else:
            # build a simulated traceback object
            tb = build_traceback(stack)
            # provide extra info if available
            extra = {}
            for key in [ 'request', 'status_code', ]:
                if key in frame.f_locals:
                    extra[key] = frame.f_locals[key]
            logger.error("Assertion failed: '%s': %s != %s", statement, repr(value), state, exc_info=(AssertionError, statement, tb), extra=extra)

def unreachable(date="(unknown)"):
    "Raises an assertion or sends traceback to admins if executed."
    stack = inspect.stack()[1:]
    frame = stack[0][0]
    if settings.DEBUG is True or settings.SERVER_MODE == 'test':
        raise AssertionError("Arrived at code in %s() which was marked unreachable on %s." % (frame.f_code.co_name, date))
    else:
        # build a simulated traceback object
        tb = build_traceback(stack)
        # provide extra info if available
        extra = {}
        for key in [ 'request', 'status_code', ]:
            if key in frame.f_locals:
                extra[key] = frame.f_locals[key]
        logger.error("Arrived at code in %s() which was marked unreachable on %s." % (frame.f_code.co_name, date),
                        exc_info=(AssertionError, frame.f_code.co_name, tb), extra=extra)
    
