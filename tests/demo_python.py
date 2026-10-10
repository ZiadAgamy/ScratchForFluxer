"""The Python file the landing page's demo shows, and the one the compiler writes for a new bot's blocks."""

DEMO_PYTHON = """import os

import fluxer

bot = fluxer.Bot(
    command_prefix="!",
    intents=fluxer.Intents.default(),
)


@bot.event
async def on_ready():
    print("Bot is online!")


@bot.command()
async def ping(ctx):
    await ctx.reply("Pong!")


bot.run(os.environ["FLUXER_TOKEN"])
"""
