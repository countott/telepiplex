"""Test helpers shared by Telepiplex feature branches."""

import sys
import types


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

    class InlineKeyboardButton(_TelegramObject):
        pass

    class InlineKeyboardMarkup(_TelegramObject):
        pass

    class Update(_TelegramObject):
        pass

    class _Handler(_TelegramObject):
        pass

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
    telegram_module.InlineKeyboardButton = InlineKeyboardButton
    telegram_module.InlineKeyboardMarkup = InlineKeyboardMarkup
    telegram_module.Update = Update
    telegram_ext_module.CallbackQueryHandler = _Handler
    telegram_ext_module.CommandHandler = _Handler
    telegram_ext_module.ContextTypes = ContextTypes
    telegram_ext_module.ConversationHandler = ConversationHandler
    telegram_ext_module.MessageHandler = _Handler
    telegram_ext_module.Application = _TelegramObject
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
