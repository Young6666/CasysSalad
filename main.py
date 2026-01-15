import discord
from discord.ext import commands, tasks
from discord import ui
import datetime
import asyncio
import json # json 모듈 임포트
import os

# ===========================
# 설정 영역 (파일에서 로드)
# ===========================

# tokens.json 파일이 있는지 확인하고 로드
try:
    with open("token.json", "r", encoding="utf-8") as f:
        tokens = json.load(f)
        
    TOKEN = tokens["TOKEN"]
    # 중요: json에서 가져온 값은 안전하게 int로 변환해줍니다.
    TARGET_CHANNEL_ID = int(tokens["TARGET_CHANNEL_ID"]) 
    
except FileNotFoundError:
    print("❌ 에러: 'tokens.json' 파일이 없습니다. 설정 파일을 만들어주세요.")
    exit()
except KeyError as e:
    print(f"❌ 에러: tokens.json 파일에 {e} 값이 빠져있습니다.")
    exit()

# ===========================


# 전역 변수 (데이터 저장용)
current_orders = {} # { '사용자닉네임': '메뉴명' } 형태의 딕셔너리
dashboard_message = None # 주문 현황판 메시지 객체를 저장할 변수

# 봇 권한 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# --- 1. UI 컴포넌트 정의 ---

# 메뉴 입력 모달 (팝업창)
class OrderModal(ui.Modal, title='🥗 점심 메뉴 입력'):
    menu_input = ui.TextInput(label='오늘 드실 메뉴를 적어주세요', placeholder='예: 닭가슴살 샐러드, 연어 포케 등')

    async def on_submit(self, interaction: discord.Interaction):
        # 제출 버튼 눌렀을 때 작동
        user_name = interaction.user.display_name
        menu_name = self.menu_input.value
        
        # 주문 저장
        current_orders[user_name] = menu_name
        
        # 사용자에게만 보이는 확인 메시지
        await interaction.response.send_message(f"✅ **{menu_name}** 주문이 접수되었습니다!", ephemeral=True)
        
        # 현황판 업데이트
        await update_dashboard_UI()

# 메인 버튼 뷰 (주문하기 / 도움말)
class PersistentOrderView(ui.View):
    def __init__(self):
        # timeout=None으로 설정해야 버튼이 영구적으로 작동합니다.
        super().__init__(timeout=None)

    @ui.button(label="🙋‍♀️ 주문하기 / 수정하기", style=discord.ButtonStyle.green, custom_id="order_btn", emoji="🥗")
    async def order_button(self, interaction: discord.Interaction, button: ui.Button):
        # 모달(팝업창) 띄우기
        await interaction.response.send_modal(OrderModal())

    @ui.button(label="ℹ️ 메뉴판/도움말", style=discord.ButtonStyle.secondary, custom_id="help_btn")
    async def help_button(self, interaction: discord.Interaction, button: ui.Button):
        # 도움말 메시지 (ephemeral=True로 본인에게만 보임)
        help_text = """
        **[ 🥗 샐러드 주문 봇 도움말 ]**
        
        1. **'주문하기'** 버튼을 눌러 드실 메뉴를 입력하세요.
        2. 메뉴를 바꾸고 싶으면 다시 버튼을 눌러 새로 입력하면 덮어씌워집니다.
        3. 주문 현황은 실시간으로 이 메시지에 업데이트됩니다.
        4. **매일 낮 12시 30분**에 주문 내역이 자동으로 초기화됩니다.
        
        (추후 여기에 실제 메뉴판 이미지나 링크를 추가할 수 있습니다.)
        """
        await interaction.response.send_message(help_text, ephemeral=True)


# --- 2. 핵심 로직 함수 ---

# 현황판(Dashboard) 메시지를 업데이트하는 함수
async def update_dashboard_UI():
    global dashboard_message
    if dashboard_message is None: return

    # 임베드(Embed) 디자인 생성
    embed = discord.Embed(title="🥗 오늘의 샐러드 주문 현황", description="아래 버튼을 눌러 주문을 입력해주세요.", color=0x57F287)
    # 이전에 생성한 샐러드 이미지 URL을 여기에 넣으면 더 예쁩니다.
    # embed.set_thumbnail(url="YOUR_IMAGE_URL") 
    
    if not current_orders:
        embed.add_field(name="현재 주문 내역", value="아직 주문이 없습니다. 텅 비었어요! 🥲", inline=False)
    else:
        order_list_str = ""
        for user, menu in current_orders.items():
            order_list_str += f"👤 **{user}**: {menu}\n"
        embed.add_field(name=f"현재 총 {len(current_orders)}명 주문 중", value=order_list_str, inline=False)
    
    now_time = datetime.datetime.now().strftime("%H:%M")
    embed.set_footer(text=f"마지막 업데이트: {now_time} | 매일 12:30 초기화")

    # 기존 메시지를 수정(edit)하여 업데이트
    await dashboard_message.edit(content=None, embed=embed, view=PersistentOrderView())


# --- 3. 봇 이벤트 및 스케줄러 ---

@bot.event
async def on_ready():
    print(f'로그인 성공: {bot.user}')
    # 봇이 재시작되어도 버튼이 동작하도록 뷰 등록
    bot.add_view(PersistentOrderView())
    # 스케줄러 시작
    scheduled_flush_task.start()

# 관리자용 초기화 명령어 (!시작)
@bot.command(name="시작")
async def start_dashboard(ctx):
    global dashboard_message
    # 지정된 채널이 맞는지 확인
    if ctx.channel.id != TARGET_CHANNEL_ID:
        await ctx.send(f"이 명령어는 지정된 채널(<#{TARGET_CHANNEL_ID}>)에서만 사용할 수 있습니다.", delete_after=5)
        return

    # 기존 메시지가 있다면 삭제 (선택사항)
    if ctx.channel.last_message and ctx.channel.last_message.author == bot.user:
        await ctx.channel.purge(limit=1)
        
    # 초기 메시지 전송 후 변수에 저장
    dashboard_message = await ctx.send("주문 시스템을 로딩 중입니다...", view=PersistentOrderView())
    # UI 업데이트 실행
    await update_dashboard_UI()


# 매일 12시 30분 자동 초기화 스케줄러 (1분마다 체크)
@tasks.loop(minutes=1)
async def scheduled_flush_task():
    # 현재 서버 시간 기준 (필요시 timezone 설정 추가 가능)
    now = datetime.datetime.now()
    
    # 매일 12시 30분에 실행
    if now.hour == 12 and now.minute == 30:
        global current_orders
        if current_orders: # 주문이 있을 때만 초기화 알림
            channel = bot.get_channel(TARGET_CHANNEL_ID)
            if channel:
                await channel.send("🕒 **오후 12시 30분!** 주문 내역이 초기화되었습니다.")
        
        # 데이터 초기화 및 UI 업데이트
        current_orders.clear()
        await update_dashboard_UI()

# 봇 실행
bot.run(TOKEN)