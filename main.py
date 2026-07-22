import os
import json
import re
import datetime
import discord
from discord.ext import commands
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- 環境変数からの設定読み込み ---
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_FORUM_IDS = [int(i.strip()) for i in os.getenv("TARGET_FORUM_IDS", "").split(",") if i.strip()]
CALENDAR_ID = os.getenv("CALENDAR_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Google Calendar API のセットアップ
SCOPES = ['https://www.googleapis.com/auth/calendar']
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=creds)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 日時解析用関数 (30分刻み・小数点対応)
def parse_schedule_text(text):
    schedules = []
    lines = text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 例: "2026-07-25 20:00, 4.5" や "2026/07/25 20:00 4.5"
        match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s+(\d{1,2}:\d{2})[,\s]+(\d+(?:\.\d+)?)', line)
        if match:
            date_str, time_str, duration_str = match.groups()
            date_str = date_str.replace('/', '-')
            
            start_dt_str = f"{date_str} {time_str}"
            start_time = datetime.datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M")
            
            # 日本時間 (JST) 設定
            jst = datetime.timezone(datetime.timedelta(hours=9))
            start_time = start_time.replace(tzinfo=jst)
            
            duration_hours = float(duration_str)
            end_time = start_time + datetime.timedelta(minutes=int(duration_hours * 60))
            
            schedules.append((start_time, end_time))
    return schedules

# 過去に作成されたGoogleカレンダー予定の一括削除
def delete_existing_events(thread_url):
    events_result = calendar_service.events().list(
        calendarId=CALENDAR_ID,
        q=thread_url
    ).execute()
    events = events_result.get('items', [])
    for event in events:
        calendar_service.events().delete(
            calendarId=CALENDAR_ID,
            eventId=event['id']
        ).execute()
    return len(events)

# 日時設定モーダル（ダイアログ）
class ScheduleModal(discord.ui.Modal, title="セッション日時の設定"):
    schedule_input = discord.ui.TextInput(
        label="開催日時と所要時間（複数行可）",
        style=discord.TextStyle.paragraph,
        placeholder="例:\n2026-07-25 20:00, 4.5\n2026-07-26 13:00, 5",
        required=True,
        max_length=500
    )

    def __init__(self, thread: discord.Thread):
        super().__init__()
        self.thread = thread

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        parsed_schedules = parse_schedule_text(self.schedule_input.value)
        if not parsed_schedules:
            await interaction.followup.send(
                "❌ 入力形式が正しく読めませんでした。\n以下の形式で入力してください：\n`YYYY-MM-DD HH:MM, 所要時間(時間)`\n例: `2026-07-25 20:00, 4.5`",
                ephemeral=True
            )
            return

        try:
            # 既存予定の上書き更新処理
            delete_existing_events(self.thread.jump_url)

            # 新規予定の作成
            created_count = 0
            for start_time, end_time in parsed_schedules:
                event_body = {
                    'summary': f"【SW2.5】{self.thread.name}",
                    'description': f"フォーラム投稿: {self.thread.jump_url}",
                    'start': {'dateTime': start_time.isoformat()},
                    'end': {'dateTime': end_time.isoformat()},
                }
                calendar_service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
                created_count += 1

            await interaction.followup.send(
                f"✅ カレンダーに **{created_count}件** のセッション日程を登録しました！",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ カレンダー登録エラー: {e}", ephemeral=True)

# 管理用操作ボタン
class ScheduleControlView(discord.ui.View):
    def __init__(self, thread: discord.Thread):
        super().__init__(timeout=None)
        self.thread = thread

    @discord.ui.button(label="📅 日時を設定・編集する", style=discord.ButtonStyle.primary)
    async def set_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleModal(self.thread))

    @discord.ui.button(label="🗑️ 予定を全削除する", style=discord.ButtonStyle.danger)
    async def delete_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            count = delete_existing_events(self.thread.jump_url)
            if count > 0:
                await interaction.followup.send(f"🗑️ カレンダーから該当セッションの予定を **{count}件** 削除しました。", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ 登録されている予定が見つかりませんでした。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 削除エラー: {e}", ephemeral=True)

@bot.event
async def on_ready():
    print(f"ログイン完了: {bot.user.name}")

@bot.event
async def on_thread_create(thread: discord.Thread):
    if thread.parent_id in TARGET_FORUM_IDS:
        view = ScheduleControlView(thread)
        await thread.send(
            "【セッションスケジュール管理】\n"
            "日程が決まりましたら下のボタンからGoogleカレンダーへ登録できます。\n"
            "※複数日程や後からの変更・削除にも対応しています。",
            view=view
        )

if __name__ == "__main__":
    bot.run(TOKEN)
