"""Test helpers shared by Telepiplex feature branches."""

import json
import sys
import types


try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    yaml_module = types.ModuleType("yaml")

    def safe_dump(data, stream=None, *args, **kwargs):
        text = json.dumps(data or {}, ensure_ascii=False)
        if stream is not None:
            stream.write(text)
            return None
        return text

    def safe_load(data):
        if hasattr(data, "read"):
            data = data.read()
        if not data:
            return {}
        return json.loads(data)

    yaml_module.safe_dump = safe_dump
    yaml_module.safe_load = safe_load
    sys.modules["yaml"] = yaml_module


try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_module = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class HTTPError(RequestException):
        pass

    def _missing_request(*args, **kwargs):
        raise RequestException("requests is not installed in this test environment")

    requests_module.get = _missing_request
    requests_module.post = _missing_request
    requests_module.RequestException = RequestException
    requests_module.HTTPError = HTTPError
    requests_module.exceptions = types.SimpleNamespace(RequestException=RequestException, HTTPError=HTTPError)
    sys.modules["requests"] = requests_module


try:
    import qrcode  # noqa: F401
except ModuleNotFoundError:
    qrcode_module = types.ModuleType("qrcode")

    class _QrImage:
        def save(self, *_args, **_kwargs):
            return None

    class QRCode:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def add_data(self, *_args, **_kwargs):
            return None

        def make(self, *_args, **_kwargs):
            return None

        def make_image(self, *_args, **_kwargs):
            return _QrImage()

    qrcode_module.QRCode = QRCode
    qrcode_module.constants = types.SimpleNamespace(ERROR_CORRECT_L=1)
    sys.modules["qrcode"] = qrcode_module


try:
    import telegram  # noqa: F401
except ModuleNotFoundError:
    telegram_module = types.ModuleType("telegram")
    telegram_ext_module = types.ModuleType("telegram.ext")
    telegram_helpers_module = types.ModuleType("telegram.helpers")
    telegram_warnings_module = types.ModuleType("telegram.warnings")
    telegram_error_module = types.ModuleType("telegram.error")

    class _TelegramObject:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class BotCommand:
        def __init__(self, command, description):
            self.command = command
            self.description = description

    class Bot(_TelegramObject):
        pass

    class InlineKeyboardButton(_TelegramObject):
        def __init__(self, text, callback_data=None, **kwargs):
            super().__init__(text, callback_data=callback_data, **kwargs)
            self.text = text
            self.callback_data = callback_data

    class InlineKeyboardMarkup(_TelegramObject):
        def __init__(self, inline_keyboard, **kwargs):
            super().__init__(inline_keyboard, **kwargs)
            self.inline_keyboard = inline_keyboard

    class Update(_TelegramObject):
        pass

    class _Handler(_TelegramObject):
        pass

    class _ApplicationBuilder:
        def token(self, _token):
            return self

        def post_init(self, _callback):
            return self

        def connect_timeout(self, _value):
            return self

        def read_timeout(self, _value):
            return self

        def write_timeout(self, _value):
            return self

        def pool_timeout(self, _value):
            return self

        def build(self):
            return _TelegramObject()

    class Application(_TelegramObject):
        @staticmethod
        def builder():
            return _ApplicationBuilder()

    class ConversationHandler(_Handler):
        END = -1

    class ContextTypes:
        DEFAULT_TYPE = object

    class _Filter:
        def __and__(self, other):
            return self

        def __invert__(self):
            return self

    class _Filters:
        TEXT = _Filter()
        COMMAND = _Filter()

        @staticmethod
        def Regex(*args, **kwargs):
            return _Filter()

    class PTBUserWarning(Warning):
        pass

    class NetworkError(Exception):
        pass

    class TimedOut(NetworkError):
        pass

    def escape_markdown(text, version=1):
        return str(text)

    telegram_module.BotCommand = BotCommand
    telegram_module.Bot = Bot
    telegram_module.InlineKeyboardButton = InlineKeyboardButton
    telegram_module.InlineKeyboardMarkup = InlineKeyboardMarkup
    telegram_module.Update = Update
    telegram_ext_module.CallbackQueryHandler = _Handler
    telegram_ext_module.CommandHandler = _Handler
    telegram_ext_module.ContextTypes = ContextTypes
    telegram_ext_module.ConversationHandler = ConversationHandler
    telegram_ext_module.MessageHandler = _Handler
    telegram_ext_module.Application = Application
    telegram_ext_module.filters = _Filters()
    telegram_helpers_module.escape_markdown = escape_markdown
    telegram_warnings_module.PTBUserWarning = PTBUserWarning
    telegram_error_module.NetworkError = NetworkError
    telegram_error_module.TimedOut = TimedOut

    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.ext"] = telegram_ext_module
    sys.modules["telegram.helpers"] = telegram_helpers_module
    sys.modules["telegram.warnings"] = telegram_warnings_module
    sys.modules["telegram.error"] = telegram_error_module
