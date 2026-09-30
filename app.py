import os
from mcp.server.fastmcp import FastMCP
from google import genai
from google.genai import types

port = int(os.environ.get("PORT", 8000))
mcp = FastMCP("Gemini Video Analyzer", port=port, host="0.0.0.0")

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@mcp.tool()
def analizar_video(url: str, instruccion: str = "Analiza detalladamente los puntos clave, marcas de tiempo y mejores momentos del video.") -> str:
    """Analiza un video de YouTube o URL multimedia usando Gemini Multimodal."""
    try:
        response = client.interactions.create(
            model="gemini-3.8-flash",
            input=[
                {"type": "text", "text": instruccion},
                {"type": "video", "uri": url, "mime_type": "video/*"}
            ]
        )
        if hasattr(response, 'output_text') and response.output_text:
            return response.output_text
        return str(response)
    except Exception as e:
        try:
            res = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    types.Part.from_uri(file_uri=url, mime_type="video/*"),
                    instruccion
                ]
            )
            return res.text
        except Exception as e2:
            return f"Error con Gemini: {str(e)} | {str(e2)}"

if __name__ == "__main__":
    mcp.run(transport="sse")
