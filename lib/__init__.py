import logging
import sys

def set_logger():
    logging_config = {"level": logging.INFO}
    stream = sys.stderr if sys.stdin.isatty() else sys.stdout
    logging_config.update({"stream": stream})
    logging_config.update({"format": "[%(asctime)s] [%(process)d:%(thread)d] %(levelname)s -- %(message)s"})
    logging.basicConfig(**logging_config)

def log_exception(err):
    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__)).strip()
    logging.error(tb_str)
    return tb_str

def exception_summary(err):
    return "".join(traceback.format_exception_only(err)).strip()

set_logger()
