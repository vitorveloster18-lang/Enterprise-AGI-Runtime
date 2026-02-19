import requests
import json
import sys

def test_ollama():
    """Testa conexão com Ollama local"""
    try:
        # Testa se Ollama está respondendo
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            print("✅ Ollama local está RODANDO!")
            print(f"📦 Modelos disponíveis: {len(models)}")
            for model in models:
                print(f"   - {model.get('name', 'unknown')}")
            return True
        else:
            print(f"⚠️ Ollama respondeu com status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Ollama NÃO está rodando na porta 11434")
        print("💡 Inicie com: ollama serve")
        return False
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_model_qwen():
    """Testa se consegue gerar com Qwen2.5:7b"""
    try:
        print("\n🧪 Testando geração com Qwen2.5:7b...")
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5:7b",
                "prompt": "Responda em português: Olá, você está funcionando?",
                "stream": False
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Qwen2.5:7b FUNCIONANDO!")
            print(f"📝 Resposta: {result.get('response', 'Sem resposta')[:100]}...")
            return True
        else:
            print(f"❌ Erro {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Erro ao testar Qwen: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Testando Ollama Local...\n")
    
    if test_ollama():
        test_model_qwen()
    else:
        print("\n💡 Para iniciar Ollama:")
        print("   1. Abra PowerShell/CMD")
        print("   2. Execute: ollama serve")
        print("   3. Ou execute: ollama run qwen2.5:7b")