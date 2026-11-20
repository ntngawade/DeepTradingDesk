"""Encrypted local Telegram settings and trade-plan notifications."""
import json
import re
import threading
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from secret_store import windows_crypt, cloud_crypt


class TelegramShare:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _crypt(self, data, decrypt=False):
        return windows_crypt(data, decrypt) if __import__('os').name == 'nt' else cloud_crypt(data, decrypt)

    def _read(self):
        if not self.path.exists():
            return {'botToken': '', 'chatId': ''}
        value = json.loads(self._crypt(self.path.read_bytes(), True))
        if not isinstance(value, dict):
            raise ValueError('Saved Telegram settings are invalid.')
        return value

    def status(self):
        with self.lock:
            config = self._read()
            return {'configured': bool(config.get('botToken') and config.get('chatId')),
                    'chatId': config.get('chatId', ''), 'tokenSaved': bool(config.get('botToken'))}

    @staticmethod
    def _api_json(request, timeout=15):
        try:
            with build_opener().open(request, timeout=timeout) as response:
                result = json.loads(response.read(500_000))
        except HTTPError as error:
            try:
                result = json.loads(error.read(100_000))
                message = result.get('description') if isinstance(result, dict) else None
            except Exception:
                message = None
            raise ValueError(message or f'Telegram returned HTTP {error.code}.') from None
        except (URLError, TimeoutError, OSError):
            raise ValueError('Could not reach the Telegram Bot API. Check this computer’s internet connection.') from None
        except Exception:
            raise ValueError('Telegram returned an unreadable response. Try again.') from None
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise ValueError(result.get('description') if isinstance(result, dict) and result.get('description') else 'Telegram rejected the request.')
        return result.get('result')

    @staticmethod
    def _get(token, method, params=None):
        query = ('?'+urlencode(params)) if params else ''
        request = Request(f'https://api.telegram.org/bot{token}/{method}{query}', method='GET')
        return TelegramShare._api_json(request)

    def check(self):
        with self.lock:
            config = self._read()
        token, chat_id = config.get('botToken', ''), config.get('chatId', '')
        if not token:
            raise ValueError('Save your Telegram bot token in Settings first.')
        if not chat_id:
            raise ValueError('Save the Telegram group ID in Settings first.')
        bot = self._get(token, 'getMe')
        chat = self._get(token, 'getChat', {'chat_id': chat_id})
        member = self._get(token, 'getChatMember', {'chat_id': chat_id, 'user_id': bot['id']})
        role = member.get('status', 'unknown')
        if chat.get('type') == 'channel':
            can_send = role in ('creator', 'administrator') and member.get('can_post_messages', role == 'creator')
        elif role == 'restricted':
            can_send = bool(member.get('is_member') and member.get('can_send_messages'))
        else:
            can_send = role in ('member', 'administrator', 'creator')
        return {'ok': True, 'botName': '@'+bot.get('username', 'bot'),
                'chatTitle': str(chat.get('title') or chat.get('username') or 'Telegram chat')[:120],
                'chatType': chat.get('type', 'unknown'), 'botStatus': role, 'canSend': can_send}

    def configure(self, token, chat_id):
        if not isinstance(token, str) or not isinstance(chat_id, str):
            raise ValueError('Invalid Telegram settings.')
        token, chat_id = token.strip(), chat_id.strip()
        with self.lock:
            try:
                current = self._read()
            except (ValueError, OSError):
                current = {}
            saved_token = current.get('botToken', '')
            if token:
                if not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{20,100}', token):
                    raise ValueError('Enter a valid Telegram bot token from BotFather.')
                saved_token = token
            elif not saved_token:
                raise ValueError('Enter your Telegram bot token once. If settings were saved under another Windows account, enter it again here to replace the unreadable copy.')
            saved_chat = chat_id or current.get('chatId', '')
            if saved_chat and not re.fullmatch(r'-?\d{5,25}|@[A-Za-z0-9_]{5,32}', saved_chat):
                raise ValueError('Enter a numeric Telegram group chat ID or public @group username.')
            payload = self._crypt(json.dumps({'botToken': saved_token, 'chatId': saved_chat}).encode())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            try:
                temporary.write_bytes(payload)
                __import__('os').replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return self.status()

    def find_chats(self):
        """Read recent bot updates and return group IDs without exposing the bot token."""
        with self.lock:
            config = self._read()
        token = config.get('botToken', '')
        if not token:
            raise ValueError('Save the bot token in Settings first.')
        updates = self._get(token, 'getUpdates', {'limit': 100, 'timeout': 0})
        found = {}
        for update in updates:
            for key in ('message', 'edited_message', 'channel_post', 'edited_channel_post', 'my_chat_member'):
                chat = (update.get(key) or {}).get('chat')
                if not isinstance(chat, dict) or chat.get('type') not in ('group', 'supergroup', 'channel'):
                    continue
                chat_id = str(chat.get('id', ''))
                if not re.fullmatch(r'-?\d{5,25}', chat_id):
                    continue
                found[chat_id] = {'chatId': chat_id, 'title': str(chat.get('title') or chat.get('username') or 'Telegram group')[:120], 'type': chat.get('type')}
        return {'chats': list(found.values())}

    def send_photo(self, caption, image, filename='chart.png'):
        if not isinstance(caption, str) or not caption.strip() or len(caption) > 1024:
            raise ValueError('Telegram photo caption must be 1–1024 characters.')
        if not isinstance(image, bytes) or not 1 <= len(image) <= 5_000_000:
            raise ValueError('Attach a chart screenshot under 5 MB.')
        if not (image.startswith(b'\x89PNG\r\n\x1a\n') or image.startswith(b'\xff\xd8\xff')):
            raise ValueError('Use a PNG or JPEG chart screenshot.')
        with self.lock:
            config = self._read()
        if not config.get('botToken') or not config.get('chatId'):
            raise ValueError('Configure the Telegram bot token and group chat ID in Settings first.')
        boundary = 'DeepTradingDeskTelegramBoundary'
        safe_name = 'chart.jpg' if str(filename).lower().endswith(('.jpg', '.jpeg')) else 'chart.png'
        mime = 'image/jpeg' if safe_name.endswith('.jpg') else 'image/png'
        chunks = []
        def field(name, value):
            chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()])
        field('chat_id', config['chatId'])
        field('caption', caption)
        field('parse_mode', 'HTML')
        chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{safe_name}"\r\nContent-Type: {mime}\r\n\r\n'.encode(), image, b'\r\n', f'--{boundary}--\r\n'.encode()])
        request = Request(f"https://api.telegram.org/bot{config['botToken']}/sendPhoto", data=b''.join(chunks),
                          headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
        result = self._api_json(request, timeout=20)
        return {'sent': True, 'messageId': (result or {}).get('message_id')}
