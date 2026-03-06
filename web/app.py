from fastapi import FastAPI, Request, Depends, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from core.database import get_db, Trade
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.state import state   # новый импорт

templates = Jinja2Templates(directory="web/templates")
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trade).order_by(Trade.timestamp.desc()).limit(20))
    trades = result.scalars().all()
    positions = []
    if state.bot and state.bot.strategies:
        for strat in state.bot.strategies:
            if strat.position:
                positions.append({
                    'symbol': strat.symbol,
                    'side': strat.position,
                    'entry_price': strat.entry_price,
                    'amount': strat.amount
                })
    return templates.TemplateResponse("index.html", {
        "request": request,
        "trades": trades,
        "positions": positions
    })

@app.post("/start_strategy")
async def start_strategy(strategy_name: str = Form(...), symbol: str = Form(...), leverage: int = Form(3)):
    if state.bot:
        await state.bot.start_strategy(strategy_name, symbol, leverage)
    return RedirectResponse(url="/", status_code=303)

@app.post("/stop_all")
async def stop_all():
    if state.bot:
        await state.bot.stop_all()
    return RedirectResponse(url="/", status_code=303)