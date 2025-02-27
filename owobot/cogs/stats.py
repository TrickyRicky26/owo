import os
from recordclass import RecordClass

os.environ['PYSPARK_SUBMIT_ARGS'] = '--packages org.apache.kudu:kudu-spark3_2.12:1.15.0 pyspark-shell'
import misc.common
import io
import threading
import discord
import plotly.express as px
from discord.ext import commands
from pyspark.shell import spark
from pyspark.sql.functions import *
from pyspark.sql.types import *
from misc import common


def get_show_string(df, n=20, truncate=True, vertical=False):
    if isinstance(truncate, bool) and truncate:
        return df._jdf.showString(n, 20, vertical)
    else:
        return df._jdf.showString(n, int(truncate), vertical)


def count_words(df) -> str:
    dfw = df.select(explode(
        split(regexp_replace(regexp_replace(lower(col("msg")), "[^a-z $]", ""), " +", " "), " ")).alias("word")) \
        .groupBy("word") \
        .count() \
        .orderBy("count", ascending=False)
    return get_show_string(dfw, n=20)

class Stats(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.spark_lock = threading.Lock()
        self.df_global = config.datalake.get_df("msgs")

    async def cog_check(self, ctx):
        if not self.spark_lock.locked():
            return True
        await ctx.channel.send(f"Sorry, another query seems to be running")
        return False

    def get_messages_by_author(self, ctx):
        return self.df_global.filter((col("author_id") == ctx.author.id) & (col("guild_id") == ctx.guild.id))

    def get_guild_df(self, ctx):
        return self.df_global.filter(col("guild_id") == ctx.guild.id)

    async def get_id_name_df(self, ctx):
        mmap = []
        for m in ctx.guild.members:
            mmap.append((common.get_nick_or_name(m), m.id))

        return spark.createDataFrame(data=mmap, schema=["name", "id"])

    @commands.group()
    async def stats(self, ctx):
        pass

    @stats.command(brief="how many msgs?")
    async def count(self, ctx):
        with self.spark_lock:
            dfa = self.get_messages_by_author(ctx).select("time")
            dft = dfa.groupBy(hour("time").alias("hour")).agg(count("time").alias("count"))
            dft = dft.orderBy("hour")
            res = get_show_string(dft, n=24)
            await ctx.channel.send(f'```\n{res}total messages: {dfa.count()}```')

    @stats.command(brief="when do you procrastinate?")
    async def activity(self, ctx):
        with self.spark_lock:
            dfa = self.get_messages_by_author(ctx).select("time")
            dft = dfa.groupBy(hour("time").alias("hour")).agg(count("time").alias("count"))
            dft = dft.orderBy("hour")
            res = get_show_string(dft, n=24)
            await ctx.channel.send(f'```\n{res}total messages: {dfa.count()}```')

    @stats.command(brief="use your words")
    async def words(self, ctx):
        with self.spark_lock:
            dfa = self.get_messages_by_author(ctx)
            res = count_words(dfa)
            await ctx.channel.send(f'```\n{misc.common.sanitize_markdown(res)}\n```')

    @stats.command(brief="words, but also use messages from dms/other guilds")
    async def simonwords(self, ctx):
        with self.spark_lock:
            dfa = self.df_global.filter(col("author_id") == ctx.author.id)
            res = count_words(dfa)
            await ctx.channel.send(f'```\n{misc.common.sanitize_markdown(res)}\n```')

    @stats.command(brief="words, but emotes", aliases=["emo"])
    async def emotes(self, ctx):
        with self.spark_lock:
            dfa = self.get_messages_by_author(ctx)
            dfw = dfa.select(explode(expr("regexp_extract_all(msg, '<a?:[^:<>@*~]+:\\\\d+>', 0)")).alias("emote")) \
                .groupBy("emote") \
                .count() \
                .orderBy("count", ascending=False) \
                .limit(20)
            pd = dfw.toPandas().sort_values(by=["count"])
            maxlen = len(str(pd["count"].max()))
            uwu = ["_ _"]
            for r in pd.itertuples():
                uwu.append(f"`{str(r[0]).rjust(maxlen, ' ')}` | {r[1]}")
            await ctx.channel.send("\n".join(uwu))

    @stats.command(brief="use your ~~words~~ letters")
    async def letters(self, ctx):
        with self.spark_lock:
            dfa = self.get_messages_by_author(ctx)
            dfl = dfa.select(explode(split(col("msg"), "")).alias("letter")) \
                .groupBy("letter") \
                .count() \
                .filter(ascii("letter") != 0) \
                .orderBy("count", ascending=False)
            res = get_show_string(dfl, n=20)
            await ctx.channel.send(f'```\n{res.replace("`", "")}\n```')

    @stats.command(brief="make history", aliases=["histowowy"])
    async def history(self, ctx, *members: discord.Member):
        with self.spark_lock:
            # gather relevant authors
            author_mappings = []
            if len(members) == 0:
                dfq = self.get_guild_df(ctx).groupBy("author_id").count().orderBy("count", ascending=False).limit(10)
                for r in dfq.collect():
                    author_mappings.append((common.get_nick_or_name(
                        await common.author_id_to_obj(self.bot, r["author_id"], ctx)), r["author_id"]))
            else:
                for m in members:
                    author_mappings.append((common.get_nick_or_name(m), m.id))
            author_mappings_df = spark.createDataFrame(data=author_mappings, schema=["name", "id"])
            # get msg counts
            dfg = self.get_guild_df(ctx).select("author_id", "time")
            dft = dfg.join(author_mappings_df.drop("name"), dfg["author_id"] == author_mappings_df["id"]) \
                .withColumn("date", date_trunc("day", "time")) \
                .groupBy("date", "author_id") \
                .agg(count("date").alias("count"))
            # fill gaps in data with zeros, replace ids with names
            dfp = dft.toPandas() \
                .pivot(index="date", columns="author_id", values="count") \
                .asfreq("1D", fill_value=0) \
                .fillna(0) \
                .reset_index() \
                .melt(id_vars=["date"]) \
                .join(author_mappings_df.toPandas().set_index("id"), on="author_id")
            fig = px.line(dfp, x="date", y="value", color="name")
            img = io.BytesIO()
            fig.write_image(img, format="p.ng", scale=3)
            img.seek(0)
            await ctx.channel.send(file=discord.File(fp=img, filename="../yeet.png"))

    @stats.command(brief="see all the lovebirbs #choo choo #ship", aliases=["ships"])
    async def couples(self, ctx):
        with self.spark_lock:
            n_limit = 30
            df_hugs = self.get_guild_df(ctx).select("author_id", "msg") \
                .withColumn("hug_target",
                            regexp_extract(
                                col("msg"), "(?<=(^\$(hug|hugc|ahug|bhug|ghug|dhug|h).{0,10})<@!?)\\d{17,19}(?=(>*.))", 0)
                            .cast(LongType())) \
                .drop("msg")
            df_hug_counts = df_hugs.filter(df_hugs["hug_target"].isNotNull()) \
                .rdd.map(lambda x: (x[0], x[1]) if x[0] > x[1] else (x[1], x[0])) \
                .toDF() \
                .groupBy(["_1", "_2"]) \
                .count() \
                .orderBy("count", ascending=False) \
                .limit(n_limit)
            author_mappings = await self.get_id_name_df(ctx)
            df_hug_counts_names = df_hug_counts.join(author_mappings, df_hug_counts["_1"] == author_mappings["id"]) \
                .withColumnRenamed("name", "#1").drop("id", "_1") \
                .join(author_mappings, df_hug_counts["_2"] == author_mappings["id"]) \
                .withColumnRenamed("name", "#2").drop("id", "_2") \
                .orderBy("count", ascending=False) \
                .select("#1", "#2", "count")
            res = get_show_string(df_hug_counts_names, n_limit)
            await ctx.channel.send(f'```\n{res.replace("`", "")}\n```')