"""C4 stand-in on port 8004 (blueprint 19.5). Run: python stubs/c4_stub.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("c3.demo.c4_stub:app", host="127.0.0.1", port=8004)
