import google.generativeai as genai

genai.configure(api_key="AIzaSyA0ylWXX6JpUYgcD3_sSleRT8kEDQoksbU")

for m in genai.list_models():
    if "generateContent" in m.supported_generation_methods:
        print(m.name)