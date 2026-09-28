import os
from mcp.server.fastmcp import FastMCP
from google import genai

port = int(os.environ.get("PORT", 8000))
mcp = FastMCP("Gemini Video Analyzer", port=port, host="0.0.0.0")

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@mcp.tool()
def analizar_video(url: str, instruccion: str = "Analiza los puntos clave, marcas de tiempo y momentos destacados del video.") -> str:
    """Analiza un video de YouTube o URL multimedia usando Gemini Multimodal."""
    try:
        response = client.interactions.create(
            model="gemini-2.5-flash",
            input=[
                {"type": "text", "text": instruccion},
                {"type": "video", "uri": url, "mime_type": "video/*"}
            ]
        )
        return response.output_text if hasattr(response, 'output_text') else str(response)
    except Exception as e:
        return f"Error con Gemini: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="sse")
