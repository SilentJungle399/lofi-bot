import aiohttp, asyncio, json, time, lavalink, base64, logging, coloredlogs

from datetime import datetime
from threading import Thread

from asyncio.events import AbstractEventLoop

from aiohttp.client import ClientSession
from aiohttp.client_ws import ClientWebSocketResponse
from aiohttp.http_websocket import WSMessage

# pip install -r requirements.txt
# run the command to install dependencies

coloredlogs.install(
	fmt = '%(asctime)s - %(levelname)s - %(message)s',
	datefmt='%d-%b-%y %H:%M:%S',
	level = logging.INFO
)

token = ''

class Bot:
	def __init__(self) -> None:
		self.token = token

		self.loop: AbstractEventLoop = asyncio.new_event_loop()
		asyncio.set_event_loop(self.loop)

		token_part0 = self.token.partition('.')[0].encode()
		self.user_id = int(base64.b64decode(token_part0 + b'=' * (3 - len(token_part0) % 3)))

		self.is_ready = False
		self.start_time = time.time()
		self.http: HTTPClient = None
		self.ws: ClientWebSocketResponse = None
		self.voice = VoiceStateManager(self)
		self.socket_handler: WebSocket = None
		self.listening = True
		self.song_cache = {}
	
	async def connect(self):
		self.lavaclient = lavalink.Client(int(self.user_id))

		self.lavaclient.add_node(
			host = "localhost",
			port = 5001,
			password = "youshallnotpass",
			region = "in"
		)

		self.http = HTTPClient(self)
		await self.http.create()
		self.ws = self.http.ws
		self.socket_handler = WebSocket(self)
		while self.listening:
			msg = await self.ws.receive()
			self.loop.create_task(self.socket_handler.handle_data(msg))

	async def disconnect(self):
		self.listening = False
		await self.ws.close(code = 1000)
		await self.http.session.close()

	def run(self):
		try:
			self.loop.run_until_complete(self.connect())
		except KeyboardInterrupt:
			print("")
			logging.log(logging.DEBUG, "Closing...")
			self.loop.run_until_complete(self.disconnect())
			quit()

class Request:
	def __init__(self, bot, route):
		self.bot = bot
		self.route = route

		self.http = self.bot.http
		self.session: ClientSession = self.http.session

		self.url = f"https://discord.com/api/v9/{self.route}"

	async def get(self, **kwargs):
		return await self.session.get(self.url, headers = self.http.headers, **kwargs)

	async def post(self, **kwargs):
		return await self.session.post(self.url, headers = self.http.headers, **kwargs)

	async def put(self, **kwargs):
		return await self.session.put(self.url, headers = self.http.headers, **kwargs)

	async def delete(self, **kwargs):
		return await self.session.delete(self.url, headers = self.http.headers, **kwargs)

	async def head(self, **kwargs):
		return await self.session.head(self.url, headers = self.http.headers, **kwargs)

	async def options(self, **kwargs):
		return await self.session.options(self.url, headers = self.http.headers, **kwargs)

	async def patch(self, **kwargs):
		return await self.session.patch(self.url, headers = self.http.headers, **kwargs)

class VoiceStateManager:
	def __init__(self, bot) -> None:
		self.bot = bot
		self.voice_states = {}

	async def set_init(self, data):
		if data['id'] not in self.voice_states:
			self.voice_states[data['id']] = {}

		for i in data['voice_states']:
			await self.set(data['id'], i)

	async def set(self, guild, data):
		self.voice_states[guild][data['user_id']] = data
	
	async def yeet(self, guild, user):
		self.voice_states[guild].pop(user)

	async def update(self, data):
		if not data['channel_id']:
			await self.yeet(data['guild_id'], data['user_id'])
		else:
			await self.set(data['guild_id'], data)

class WebSocket:
	DISPATCH           = 0
	HEARTBEAT          = 1
	IDENTIFY           = 2
	PRESENCE           = 3
	VOICE_STATE        = 4
	VOICE_PING         = 5
	RESUME             = 6
	RECONNECT          = 7
	REQUEST_MEMBERS    = 8
	INVALIDATE_SESSION = 9
	HELLO              = 10
	HEARTBEAT_ACK      = 11
	GUILD_SYNC         = 12

	def __init__(self, bot) -> None:
		self.bot: Bot = bot
		self.ws: ClientWebSocketResponse = self.bot.ws
		self.hearbeat_interval: int = None
		self.event_manager = Manager(self.bot)
		self.last_seq: int = None

	async def handle_data(self, msg: WSMessage):
		try:
			data = json.loads(msg.data) if msg.data else None
		except Exception as e:
			print(e)
			print(msg.data)

		if not data:
			return logging.info("Gateway connection closed!")

		await self.bot.lavaclient.voice_update_handler(data)

		self.last_seq = data['s'] if 's' in data else None

		if data['op'] == self.HELLO:
			self.hearbeat_interval = data['d']['heartbeat_interval']
			await self.start_heartbeating()
			await self.identify_self()

		elif data['op'] == self.DISPATCH:
			if data['t'] == 'MESSAGE_CREATE':
				await self.event_manager.on_message(data['d'])

			elif data['t'] == 'READY':
				self.bot.is_ready = True
				await self.event_manager.on_ready()

			elif data['t'] == 'GUILD_CREATE':
				fmt = datetime.fromisoformat(data['d']['joined_at']).timestamp()

				await self.bot.voice.set_init(data['d'])

				if int(fmt) > int(self.bot.start_time):
					logging.info(f"Joined new guild | ID: {data['d']['id']}")
					await self.event_manager.on_guild_join(data['d'])

			elif data['t'] == 'VOICE_STATE_UPDATE':
				await self.bot.voice.update(data['d'])

			elif data['t'] == 'INTERACTION_CREATE':
				await self.event_manager.on_interaction(data['d'])

			else:
				logging.debug(f"Received: {data['t']}")

	async def identify_self(self):
		await self.ws.send_str(json.dumps({
			"op": self.IDENTIFY,
			"d": {
				"token": self.bot.token,
				"intents": 643,
				"properties": {
					"$os": "windows",
					"$browser": "custom_library",
					"$device": "custom_library"
				}
			}
		}))

	async def start_heartbeating(self):
		ret = json.dumps({
			'op': self.HEARTBEAT,
			'd': self.last_seq
		})

		await self.ws.send_str(ret)

		async def runner():
			while True:
				await asyncio.sleep(self.hearbeat_interval/1000)
				await self.ws.send_str(ret)

		def conv():
			self.bot.loop.create_task(runner())

		Thread(target=conv).start()

	async def join_vc(self, guild, channel):
		ret = {
			'op': self.VOICE_STATE,
			'd': {
				'guild_id': guild,
				'channel_id': channel,
				'self_mute': False,
				'self_deaf': True
			}
		}

		await self.ws.send_json(ret)

class HTTPClient:
	def __init__(self, bot) -> None:
		self.bot = bot

		self.session: ClientSession = None
		self.ws: ClientWebSocketResponse = None
		self.headers = {
			"Authorization": f"Bot {self.bot.token}",
			"User-Agent": f"DiscordBot ($url, 1.0.0)",
			"Content-Type": "application/json"
		}

	async def create(self):
		self.session = aiohttp.ClientSession(loop = self.bot.loop)
		self.ws = await self.session.ws_connect('wss://gateway.discord.gg/?v=9&encoding=json')

	async def send_message(self, channel = None, **kwargs):
		return await Request(self.bot, f"/channels/{channel}/messages").post(json = kwargs)
	
	async def edit_message(self, channel = None, message = None, **kwargs):
		return await Request(self.bot, f'/channels/{channel}/messages/{message}').patch(json = kwargs)

	async def trigger_typing(self, channel = None):
		return await Request(self.bot, f'/channels/{channel}/typing').post()

	async def reply_interaction(self, id = None, token = None, **kwargs):
		return await Request(self.bot, f'/interactions/{id}/{token}/callback').post(json = kwargs)

	async def edit_def_reply(self, id = None, token = None, **kwargs):
		await Request(self.bot, f'/webhooks/{id}/{token}/messages/@original').patch(json = kwargs)

class Manager:
	def __init__(self, bot: Bot) -> None:
		self.bot = bot
		self.ws = self.bot.ws
		self.http = self.bot.http

	async def on_guild_join(self, data):
		resp = await Request(self.bot, f"/applications/{self.bot.user_id}/guilds/{data['id']}/commands").post(json = {
			"name": "play",
			"type": 1,
			"description": "Join a voice channel and use the command to play lo-fi music!"
		})
		resp = await resp.json()

		logging.debug(resp)

	async def on_interaction(self, data):
		if data['data']['name'] == "play":
			await self.http.reply_interaction(
				id = data['id'],
				token = data['token'],
				type = 5
			)

			vdata = self.bot.voice.voice_states[data['guild_id']]
			if data['member']['user']['id'] in vdata:
				channel = vdata[data['member']['user']['id']]['channel_id']
			else:
				return await self.http.edit_def_reply(
					id = self.bot.user_id,
					token = data['token'],
					content = "You are not in a voice channel!"
				)

			if str(self.bot.user_id) not in vdata:
				await self.bot.socket_handler.join_vc(guild = int(data['guild_id']), channel = int(channel))
			else:
				if vdata[str(self.bot.user_id)]['channel_id'] != str(channel):
					return await self.http.edit_def_reply(
						id = self.bot.user_id,
						token = data['token'],
						content = "You are not in my voice channel!"
					)

			player = self.bot.lavaclient.player_manager.create(int(data['guild_id']))
			tracks = (await player.node.get_tracks("https://www.youtube.com/watch?v=5qap5aO4i9A"))['tracks']

			player.add(str(data['member']['user']['id']), tracks[0])
			if not player.is_playing:
				await player.play()

			await self.http.edit_def_reply(
				id = self.bot.user_id,
				token = data['token'],
				content = "Playing lo-fi music..."
			)

	async def on_message(self, message):
		if message['content'].startswith("?play"):
			msg = await self.http.send_message(
				channel = message['channel_id'],
				content = "Processing request..."
			)
			d = await msg.json()

			player = self.bot.lavaclient.player_manager.create(int(message['guild_id']))
			tracks = (await player.node.get_tracks("https://www.youtube.com/watch?v=5qap5aO4i9A"))['tracks']

			vdata = self.bot.voice.voice_states[message['guild_id']]
			if message['author']['id'] in vdata:
				channel = vdata[message['author']['id']]['channel_id']
			else:
				return await self.http.edit_message(
					channel = d['channel_id'],
					message = d['id'],
					content = "You are not in a voice channel!",
				)

			if str(self.bot.user_id) not in vdata:
				await self.bot.socket_handler.join_vc(guild = int(message['guild_id']), channel = int(channel))
			else:
				if vdata[str(self.bot.user_id)]['channel_id'] != str(channel):
					return await self.http.edit_message(
						channel = d['channel_id'],
						message = d['id'],
						content = "You are not in my voice channel!",
					)

			player.add(str(message['author']['id']), tracks[0])
			if not player.is_playing:
				await player.play()

			await self.http.edit_message(
				channel = d['channel_id'],
				message = d['id'],
				content = "Playing...",
			)

	async def on_ready(self):
		logging.info("The bot is ready!")

Bot().run()
