# Fast API application entry point 

from fastapi import FastAPI

title = "Agentic AI GitHub Pipeline Recovery Agent"
app = FastAPI(title=title) # initialize FastAPI application

# health check point to check if the application is running
# this will check if fastApi, package imports are installed correctly 
# and later the deploy/CI setup can actually reach the app. 
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}