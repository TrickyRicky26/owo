import re
import discord
import emoji

from misc.db import Owner


def get_nick_or_name(user: object) -> str:
    gnick = user.nick if isinstance(user, discord.Member) and not user.nick is None else user.name
    return re.sub(r'[@!$`]', "", gnick)


async def author_id_to_obj(bot, author_id, ctx):
    author = ctx.guild.get_member(author_id)
    if author is None:
        author = await bot.fetch_user(author_id)
    return author


markdown_chars = ["~", "_", "*", "`"]


def sanitize_markdown(str: str) -> str:
    # prepend each markdown interpretable char with a zero width space
    # italics with a single * seems to not break codeblocks
    for c in markdown_chars:
        if c in str:
            str = str.replace(c, f"​{c}")
    return str


def sanitize_send(str: str) -> str:
    if str[0] in emoji.EMOJI_UNICODE_ENGLISH:
        return str
    return re.sub(r"^[^\w<>()@]+", "", str)


async def is_owner(ctx):
    if Owner.select().where(Owner.snowflake == ctx.author.id).exists():
        return True
    await react_failure(ctx)
    return False


async def react_success(ctx):
    await ctx.message.add_reaction("✅")


async def react_failure(ctx):
    await ctx.message.add_reaction("❌")


async def try_exe_cute_query(ctx, query):
    try:
        res = query.execute()
        await react_success(ctx)
        return res
    except:
        await react_failure(ctx)
