import asyncio
import re
import time as current_time
from abc import ABC
from datetime import timedelta

import discord
from redbot.core import commands
from redbot.core.commands import parse_timedelta
from redbot.core.utils.chat_formatting import humanize_timedelta
from redbot.core.utils.predicates import MessagePredicate

from ..abc import CompositeMetaClass, MixinMeta
from ..pcx_lib import delete, embed_splitter


class ReminderCommands(MixinMeta, ABC, metaclass=CompositeMetaClass):
    def __init__(self):
        self.split_all = re.compile(r"(\s+)")
        self.alpha_num = re.compile(r"[\W_]+")

    @commands.group()
    async def reminder(self, ctx: commands.Context):
        """Manage your reminders."""

    @reminder.command(aliases=["get"])
    async def list(self, ctx: commands.Context, sort: str = "time"):
        """Show a list of all of your reminders.

        Sort can either be:
        `time` (default) for soonest expiring reminder first,
        `added` for ordering by when the reminder was added,
        `id` for ordering by ID
        """
        author = ctx.message.author
        to_send = await self.get_user_reminders(author.id)
        if sort == "time":
            to_send.sort(key=lambda reminder_info: reminder_info["FUTURE"])
        elif sort == "added":
            pass
        elif sort == "id":
            to_send.sort(key=lambda reminder_info: reminder["USER_REMINDER_ID"])
        else:
            await self._send_message(
                ctx,
                "That is not a valid sorting option. Choose from `time` (default), `added`, or `id`.",
            )
            return

        if not to_send:
            await self._send_message(ctx, "You don't have any upcoming reminders.")
            return

        embed = discord.Embed(
            title=f"Reminders for {author.display_name}",
            color=await ctx.embed_color(),
        )
        embed.set_thumbnail(url=author.avatar_url)
        current_timestamp = int(current_time.time())
        for reminder in to_send:
            delta = reminder["FUTURE"] - current_timestamp
            embed.add_field(
                name="ID# {} — {}".format(
                    reminder["USER_REMINDER_ID"],
                    "In {}".format(humanize_timedelta(seconds=delta))
                    if delta > 0
                    else "Now!",
                ),
                value=reminder["REMINDER"],
                inline=False,
            )
        try:
            await embed_splitter(embed, author)
            await ctx.tick()
        except discord.Forbidden:
            await self._send_message(ctx, "I can't DM you...")

    @reminder.command(aliases=["add"])
    async def create(self, ctx: commands.Context, *, time_and_optional_text: str = ""):
        """Create a reminder with optional reminder text.

        Same as `[p]remindme`, so check that for usage help.
        """
        await self._create_reminder(ctx, time_and_optional_text)

    @reminder.group(aliases=["edit"])
    async def modify(self, ctx: commands.Context):
        """Modify an existing reminder."""
        pass

    @modify.command()
    async def time(self, ctx: commands.Context, reminder_id: int, *, time: str):
        """Modify the time of an existing reminder."""
        users_reminders = await self.get_user_reminders(ctx.message.author.id)
        old_reminder = self._get_reminder(users_reminders, reminder_id)
        if not old_reminder:
            await self._send_non_existant_msg(ctx, reminder_id)
            return
        try:
            time_delta = parse_timedelta(time, minimum=timedelta(minutes=1))
            if not time_delta:
                await ctx.send_help()
                return
        except commands.BadArgument as ba:
            await self._send_message(ctx, str(ba))
            return
        seconds = time_delta.total_seconds()
        future = int(current_time.time() + seconds)
        future_text = humanize_timedelta(timedelta=time_delta)

        new_reminder = old_reminder.copy()
        new_reminder.update(FUTURE=future, FUTURE_TEXT=future_text)
        async with self.config.reminders() as current_reminders:
            current_reminders.remove(old_reminder)
            current_reminders.append(new_reminder)
        await self._send_message(
            ctx,
            f"Reminder with ID# **{reminder_id}** has been edited successfully, "
            f"and will now remind you {future_text} from now.",
        )

    @modify.command()
    async def text(self, ctx: commands.Context, reminder_id: int, *, text: str):
        """Modify the text of an existing reminder."""
        users_reminders = await self.get_user_reminders(ctx.message.author.id)
        old_reminder = self._get_reminder(users_reminders, reminder_id)
        if not old_reminder:
            await self._send_non_existant_msg(ctx, reminder_id)
            return
        text = text.strip()
        if len(text) > 1000:
            await self._send_message(ctx, "Your reminder text is too long.")
            return

        new_reminder = old_reminder.copy()
        new_reminder.update(REMINDER=text)
        async with self.config.reminders() as current_reminders:
            current_reminders.remove(old_reminder)
            current_reminders.append(new_reminder)
        await self._send_message(
            ctx,
            f"Reminder with ID# **{reminder_id}** has been edited successfully.",
        )

    @reminder.command(aliases=["delete", "del"])
    async def remove(self, ctx: commands.Context, index: str):
        """Delete a reminder.

        <index> can either be:
        - a number for a specific reminder to delete
        - `last` to delete the most recently created reminder
        - `all` to delete all reminders (same as [p]forgetme)
        """
        await self._delete_reminder(ctx, index)

    @commands.command()
    async def remind(
        self, ctx: commands.Context, *, time_and_optional_text: str = ""
    ):
        """Create a reminder with optional reminder text.

        Either of the following formats are allowed:
        `[p]remindme [in] <time> [to] [reminder_text]`
        `[p]remindme [to] [reminder_text] [in] <time>`

        `<time>` supports commas, spaces, and "and":
        `12h30m`, `6 hours 15 minutes`, `2 weeks, 4 days, and 10 seconds`
        Accepts seconds, minutes, hours, days, and weeks

        Examples:
        `[p]remindme in 8min45sec to do that thing`
        `[p]remindme to water my plants in 2 hours`
        `[p]remindme in 3 days`
        `[p]remindme 8h`
        """
        await self._create_reminder(ctx, time_and_optional_text)

    @commands.command()
    async def forgetme(self, ctx: commands.Context):
        """Remove all of your upcoming reminders."""
        await self._delete_reminder(ctx, "all")

    async def _create_reminder(
        self, ctx: commands.Context, time_and_optional_text: str
    ):
        """Logic to create a reminder."""
        author = ctx.message.author
        maximum = await self.config.max_user_reminders()
        users_reminders = await self.get_user_reminders(author.id)
        if len(users_reminders) > maximum - 1:
            plural = "reminder" if maximum == 1 else "reminders"
            await self._send_message(
                ctx,
                "You have too many reminders! "
                f"I can only keep track of {maximum} {plural} for you at a time.",
            )
            return

        # Supported:
        # [p]remindme in {time} to {text}
        # [p]remindme in {time}    {text}
        # [p]remindme in {time}
        # [p]remindme    {time} to {text}
        # [p]remindme    {time}    {text}
        # [p]remindme    {time}
        # [p]remindme to {text} in {time}
        # [p]remindme to {text}    {time}
        # [p]remindme    {text} in {time}
        # [p]remindme    {text}    {time}

        # find the time delta(s) in the text
        time = ""
        index = -1
        time_index_start = -1
        time_index_end = -1
        prev_num = ""
        full_split = self.split_all.split(time_and_optional_text.strip())
        for chunk in full_split:
            index += 1
            if chunk.isspace():
                continue
            chunk = self.alpha_num.sub("", chunk)
            if chunk == "and" and not prev_num:
                # "and" can appear between time deltas
                continue
            if chunk.isdigit():
                prev_num = chunk
                if time_index_start == -1:
                    time_index_start = index
                continue
            if chunk.isalpha() and prev_num:
                chunk = f"{prev_num} {chunk}"
            try:
                if parse_timedelta(chunk):
                    time = f"{time} {chunk}" if time else chunk
                    if time_index_start == -1:
                        time_index_start = index
                    time_index_end = index
                elif time:
                    break
                else:
                    time_index_start = -1
            except commands.BadArgument:
                pass
            prev_num = ""
        if not time:
            await ctx.send_help()
            return
        # At this point we have a time string able to be parsed for a time delta,
        # as well as the starting and ending index of where it appeared in the text

        # detect preceding "in" so it can be removed from text as well
        if (
            time_index_start > 1
            and self.alpha_num.sub("", full_split[time_index_start - 2]) == "in"
        ):
            time_index_start -= 2
        # the time portion of the text must now either be at the beginning or the end
        if time_index_start != 0 and time_index_end != len(full_split) - 1:
            await ctx.send_help()
            return
        # delete time (and optional "in", and surrounding space) from the text
        del full_split[
            max(time_index_start - 1, 0) : min(time_index_end + 2, len(full_split))
        ]
        # if the text begins with an optional "to", delete that as well
        if len(full_split) > 1 and self.alpha_num.sub("", full_split[0]) == "to":
            del full_split[0:2]

        # parse resulting time delta
        try:
            time_delta = parse_timedelta(time, minimum=timedelta(minutes=1))
        except commands.BadArgument as ba:
            await self._send_message(ctx, str(ba))
            return
        # recreate cleaned up text
        text = "".join(full_split).strip()
        if len(text) > 1000:
            await self._send_message(ctx, "Your reminder text is too long.")
            return

        seconds = time_delta.total_seconds()
        future = int(current_time.time() + seconds)
        future_text = humanize_timedelta(timedelta=time_delta)
        next_reminder_id = self.get_next_user_reminder_id(users_reminders)

        reminder = {
            "USER_REMINDER_ID": next_reminder_id,
            "USER_ID": author.id,
            "REMINDER": text,
            "FUTURE": future,
            "FUTURE_TEXT": future_text,
            "JUMP_LINK": ctx.message.jump_url,
        }
        async with self.config.reminders() as current_reminders:
            current_reminders.append(reminder)
        await self._send_message(
            ctx, f"I will remind you of {'that' if text else 'this'} in {future_text}."
        )

        if (
            ctx.guild
            and await self.config.guild(ctx.guild).me_too()
            and ctx.channel.permissions_for(ctx.me).add_reactions
        ):
            query: discord.Message = await ctx.send(
                "If anyone else would like to be reminded as well, click the bell below!"
            )
            self.me_too_reminders[query.id] = reminder
            await query.add_reaction(self.reminder_emoji)
            await asyncio.sleep(43200)
            await delete(query)
            del self.me_too_reminders[query.id]

    async def _delete_reminder(self, ctx: commands.Context, index: str):
        """Logic to delete reminders."""
        if not index:
            return
        author = ctx.message.author
        users_reminders = await self.get_user_reminders(author.id)

        if not users_reminders:
            await self._send_message(ctx, "You don't have any upcoming reminders.")
            return

        if index == "all":
            # Ask if the user really wants to do this
            pred = MessagePredicate.yes_or_no(ctx)
            await self._send_message(
                ctx,
                "Are you **sure** you want to remove all of your reminders? (yes/no)",
            )
            try:
                await ctx.bot.wait_for("message", check=pred, timeout=30)
            except asyncio.TimeoutError:
                pass
            if pred.result:
                pass
            else:
                await self._send_message(ctx, "I have left your reminders alone.")
                return
            await self._do_reminder_delete(users_reminders)
            await self._send_message(ctx, "All of your reminders have been removed.")
            return

        if index == "last":
            reminder_to_delete = users_reminders[len(users_reminders) - 1]
            await self._do_reminder_delete(reminder_to_delete)
            await self._send_message(
                ctx,
                "Your most recently created reminder (ID# **{}**) has been removed.".format(
                    reminder_to_delete["USER_REMINDER_ID"]
                ),
            )
            return

        try:
            int_index = int(index)
        except ValueError:
            await ctx.send_help()
            return

        reminder_to_delete = self._get_reminder(users_reminders, int_index)
        if reminder_to_delete:
            await self._do_reminder_delete(reminder_to_delete)
            await self._send_message(
                ctx, f"Reminder with ID# **{int_index}** has been removed."
            )
        else:
            await self._send_non_existant_msg(ctx, int_index)

    async def _do_reminder_delete(self, reminders):
        """Actually delete a reminder."""
        if not reminders:
            return
        if not isinstance(reminders, list):
            reminders = [reminders]
        async with self.config.reminders() as current_reminders:
            for reminder in reminders:
                current_reminders.remove(reminder)

    async def _send_non_existant_msg(self, ctx: commands.Context, reminder_id: int):
        """Send a message telling the user the reminder ID does not exist."""
        await self._send_message(
            ctx,
            f"Reminder with ID# **{reminder_id}** does not exist! "
            "Check the reminder list and verify you typed the correct ID#.",
        )

    @staticmethod
    def _get_reminder(reminder_list, reminder_id: int):
        """Get the reminder from reminder_list with the specified reminder_id."""
        for reminder in reminder_list:
            if reminder["USER_REMINDER_ID"] == reminder_id:
                return reminder
        return None

    @staticmethod
    async def _send_message(ctx: commands.Context, message: str):
        """Send a message.

        This will append the users name if we are sending to a channel,
        or leave it as-is if we are in a DM
        """
        if ctx.guild is not None:
            if message[:2].lower() != "i " and message[:2].lower() != "i'":
                message = message[0].lower() + message[1:]
            message = ctx.message.author.mention + ", " + message

        await ctx.send(message)
