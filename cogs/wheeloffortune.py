import asyncio
from collections import Counter, defaultdict, namedtuple
import csv
import os
from random import choice, sample
import time

import chardet
import discord
from discord.ext import commands

from .utils.dataIO import dataIO
from .utils import checks
from .utils.chat_formatting import box


DEFAULTS = {"MAX_SCORE"     : 10,
            "TIMEOUT"       : 120,
            "BOT_PLAYS"     : False,
            "LETTER_SPACING": 4,
            "REVEAL_ANSWER" : True}

WheelOfFortuneLine = namedtuple("WheelOfFortuneLine", "category answer")


class WheelOfFortune:
    def __init__(self, bot):
        self.bot = bot
        self.wheel_of_fortune_sessions = []
        self.file_path = "data/wheeloffortune/settings.json"
        settings = dataIO.load_json(self.file_path)
        self.settings = defaultdict(lambda: DEFAULTS.copy(), settings)

    @commands.group(pass_context=True, no_pm=True, aliases=['wheeloffortune_set', 'wofset'])
    @checks.mod_or_permissions(administrator=True)
    async def wheel_of_fortune_set(self, ctx):
        """Change Wheel Of Fortune settings"""
        server = ctx.message.server
        if ctx.invoked_subcommand is None:
            setings = self.settings[server.id]
            msg = box("Red gains points: {BOT_PLAYS}\n"
                      "Points to win: {MAX_SCORE}\n"
                      "".format(**settings))
            msg += "\nSee {}help triviaset to edit the settings".format(ctx.prefix)
            await self.bot.say(msg)

    @wheel_of_fortune_set.command(pass_context=True)
    async def maxscore(self, ctx, score : int):
        """Pints required to win"""
        server = ctx.message.server
        if score > 0:
            self.settings[server.id]["MAX_SCORE"] = score
            self.save_settings()
            await self.bot.say("Points required to win set to {}".format(score))
        else:
            await self.bot.say("Score must be superior to 0.")

    @wheel_of_fortune_set.command(pass_context=True)
    async def boyplays(self, ctx):
        """Red gains points"""
        server = ctx.message.server
        if self.settings[server.id]["BOT_PLAYS"]:
            self.settings[server.id]["BOT_PLAYS"] = False
            await self.bot.say("Alright, I won't embarass you at Wheel Of Fortune anymore.")
        else:
            self.settings[server.id]["BOT_PLAYS"] = True
            await slf.bot.say("I'll gain a point everytime you don't answer in time.")
        self.save_settings()

    @commands.group(pass_context=True, invoke_without_command=True, no_pm=True, aliases=['wheeloffortune', 'wof'])
    async def wheel_of_fortune(self, ctx):
        """Start a Wheel Of Fortune session"""
        message = ctx.message
        server = message.server
        session = self.get_wheel_of_fortune_by_channel(message.channel)
        if not session:
            try:
                wheel_of_fortune_list = self.parse_wheel_of_fortune_list()
            except FileNotFoundError:
                await self.bot.say("Wheel Of Fortune list not found.")
            except Exception as e:
                print(e)
                await self.bot.say("Error loading the Wheel Of Fortune list.")
            else:
                settings = self.settings[server.id]
                wofs = WheelOfFortuneSession(self.bot, wheel_of_fortune_list, message, settings)
                self.wheel_of_fortune_sessions.append(wofs)
                await wofs.new_question()
        else:
            await self.bot.say("A Wheel Of Fortune session is already ongoing in this channel.")

    @wheel_of_fortune.group(name="stop", pass_context=True, no_pm=True)
    async def wheel_of_fortune_stop(self, ctx):
        """Stops an ongoing trivia session"""
        author = ctx.message.author
        server = author.server
        admin_role = self.bot.settings.get_server_admin(server)
        mod_role = self.bot.settings.get_server_mod(server)
        is_admin = discord.utils.get(author.roles, name=admin_role)
        is_mod = discord.utils.get(author.roles, name=mod_role)
        is_owner = author.id == self.bot.settings.owner
        is_server_owner = author == server.owner
        is_authorized = is_admin or is_mod or is_owner or is_server_owner

        session = self.get_wheel_of_fortune_by_channel(ctx.message.channel)
        if session:
            if author == session.starter or is_authorized:
                await session.end_game()
                await self.bot.say("Wheel Of Fortune stopped.")
            else:
                await self.bot.say("You are not allowed to do that.")
        else:
            await self.bot.say("There's no Wheel Of Fortune session ongoing in this channel.")

    def parse_wheel_of_fortune_list(self):
        path = "data/wheeloffortune/list.csv"
        parsed_list = []

        with open(path, "rb") as f:
            try:
                encoding = chardet.detect(f.read())["encoding"]
            except:
                encoding = "ISO-8859-1"

        with open(path, "r", encoding=encoding) as f:
            csvreader = csv.reader(f)
            parsed_list = list(csvreader)

        if not parsed_list:
            raise ValueError("Empty trivia list")

        return parsed_list

    def get_wheel_of_fortune_by_channel(self, channel):
        for wof in self.wheel_of_fortune_sessions:
            if wof.channel == channel:
                return wof
        return None

    async def on_message(self, message):
        session = self.get_wheel_of_fortune_by_channel(message.channel)
        if session:
            await session.check_answer(message)

    async def on_wheel_of_fortune_end(self, instance):
        if instance in self.wheel_of_fortune_sessions:
            self.wheel_of_fortune_sessions.remove(instance)

    def save_settings(self):
        dataIO.save_json(self.file_path, self.settings)


class WheelOfFortuneSession():
    def __init__(self, bot, wheel_of_fortune_list, message, settings):
        self.bot = bot
        self.reveal_messages = ("I know this one! {}!",
                                "Easy: {}.",
                                "Oh really? It's {} of course.")
        self.fail_messages = ("To the next one I guess...",
                              "Moving on...",
                              "I'm sure you'll know the answer of the next one.",
                              "\N{PENSIVE FACE} Next one.")
        self.current_phrase = None
        self.current_missing = []
        self.phrase_list = wheel_of_fortune_list
        self.channel = message.channel
        self.starter = message.author
        self.scores = Counter()
        self.status = "new question"
        self.timer = None
        self.timeout= time.perf_counter()
        self.count = 0
        self.settings = settings
        self.last_printed_phrase = None

    async def stop_wheel_of_fortune(self):
        self.status = "stop"
        self.bot.dispatch("wheel_of_fortune_end", self)

    async def end_game(self):
        self.status = "stop"
        if self.scores:
            await self.send_table()
        self.bot.dispatch("wheel_of_fortune_end", self)

    async def new_question(self):
        # end if no more phrases
        if not self.phrase_list:
            await self.bot.say("No more phrases, game is over!")
            await self.end_game()
            return True

        # get a new phrase
        current_line = choice(self.phrase_list)
        self.current_category, self.current_phrase = current_line
        self.phrase_list.remove(current_line)
        self.current_missing = set(x for x in self.current_phrase if x.isalpha())
        self.status = "waiting for answer"
        self.count += 1

        self.timer = int(time.perf_counter())
        msg = "**Phrase number {}!**".format(self.count)
        await self.bot.say(msg)

        await self.print_phrase()

        # slowly release numbers, sleep a little at a time so correct answers
        # are responsive
        sleep_time = 1
        slept_time = 0
        while self.status == "waiting for answer" and self.current_missing:
            await asyncio.sleep(sleep_time)
            slept_time += sleep_time
            if abs(self.timeout - int(time.perf_counter())) >= self.settings["TIMEOUT"]:
                await self.bot.say("Guys...? Well, I guess I'll stop then.")
                await self.stop_wheel_of_fortune()
                return True
            if slept_time >= self.settings["LETTER_SPACING"]:
                if self.status != "waiting for answer" or not self.current_missing:
                    break
                self.current_missing = sample(self.current_missing, max(len(self.current_missing) - 1, 0))
                slept_time = 0
                await self.print_phrase()

        # check if game was killed
        if self.status == "stop":
            await self.bot.say("Done so soon? Enjoy shooting boys!")
            return True

        self.last_printed_phrase = None
        await self.bot.say("The answer was:")
        self.current_missing = []
        await self.print_phrase()
        self.last_printed_phrase = None

        # give bot a point if no one guessed
        if not self.current_missing:
            self.status = "new question"
            if self.settings["BOT_PLAYS"]:
                msg = "**+1** for me!"
                await self.bot.say(msg)
                self.scores[self.bot.user] += 1
            self.current_phrase = None
            await self.bot.type()

        # check if answer was guessed
        for score in self.scores.values():
            if score == self.settings["MAX_SCORE"]:
                await self.end_game()
                return True


        await self.bot.say("Current score is:")
        await self.send_table()


        # start next question
        if self.status == "correct answer":
            self.status = "new question"

        await asyncio.sleep(3)

        # we need to recheck because we slept and it's been a while
        if self.status == "stop":
            await self.bot.say("Done so soon? Enjoy shooting boys!")
            return True

        await self.new_question()

    async def print_phrase(self):
        top_bar = "╔{}╗".format("╤".join("═" for _ in range(len(self.current_phrase))))
        printed_phrase = "║{}║".format("│".join("█" if x in self.current_missing else x for x in self.current_phrase))
        bottom_bar = "╚{}╝".format("╧".join("═" for _ in range(len(self.current_phrase))))
        msg = box("{}\n{}\n{}\n\n{}".format(top_bar, printed_phrase, bottom_bar, self.current_category))
        if self.last_printed_phrase is None:
            self.last_printed_phrase = await self.bot.say(msg)
            await self.bot.edit_message(self.last_printed_phrase, msg)
        else:
            await self.bot.edit_message(self.last_printed_phrase, msg)

    async def send_table(self):
        t = "+ Results: \n\n"
        for user, score in self.scores.most_common():
            t += "+ {}\t{}\n".format(user, score)
        await self.bot.say(box(t, lang="diff"))

    async def check_answer(self, message):
        if self.last_printed_phrase is not None and message.id != self.last_printed_phrase.id:
            self.last_printed_phrase = None
        if message.author == self.bot.user:
            return
        elif self.current_phrase is None:
            return

        self.timeout = time.perf_counter()
        has_guessed = False

        answer = self.current_phrase.lower()
        guess = message.content.lower()
        if answer == guess:
            has_guessed = True

        if has_guessed and self.status == "waiting for answer" and self.current_missing:
            self.scores[message.author] += len(self.current_missing)
            self.status = "correct answer"
            msg = "You got it {}! **+{}** to you!".format(message.author.name, len(self.current_missing))
            await self.bot.send_message(message.channel, msg)


def check_folders():
    folders = ("data", "data/wheeloffortune/")
    for folder in folders:
        if not os.path.exists(folder):
            print("Creating " + folder + " folder...")
            os.makedirs(folder)


def check_files():
    if not os.path.isfile("data/wheeloffortune/settings.json"):
        print("Creating empty settings.json...")
        dataIO.save_json("data/wheeloffortune/settings.json", {})


def setup(bot):
    check_folders()
    check_files()
    bot.add_cog(WheelOfFortune(bot))
