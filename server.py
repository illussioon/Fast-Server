import os
import sys
import configparser
import importlib.util
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

class PluginManager:
    """Менеджер для динамической загрузки плагинов"""
    
    def __init__(self, plugin_dir="plugin"):
        self.plugin_dir = Path(plugin_dir)
        self.plugins = {}
        self.app = None
        
    def load_plugins(self, app: FastAPI):
        """Загружает все плагины из директории plugin"""
        self.app = app
        
        if not self.plugin_dir.exists():
            print(f"Директория плагинов {self.plugin_dir} не найдена")
            return
            
        # Сначала загружаем специфические плагины (ILL, TTS, AntiPublic-Web), потом GitHub, потом остальные
        plugin_order = ["ILL", "TTS", "AntiPublic-Web"]
        loaded_plugins = set()
        
        # Загружаем плагины в определенном порядке
        for plugin_name in plugin_order:
            plugin_path = self.plugin_dir / plugin_name
            if plugin_path.exists() and plugin_path.is_dir():
                self._load_plugin(plugin_path)
                loaded_plugins.add(plugin_name)
        
        # Загружаем GitHub плагин отдельно, чтобы он мог проверить существующие маршруты
        github_plugin_path = self.plugin_dir / "GitHub"
        if github_plugin_path.exists() and github_plugin_path.is_dir():
            self._load_plugin(github_plugin_path)
            loaded_plugins.add("GitHub")

        # Загружаем остальные плагины
        for plugin_path in self.plugin_dir.iterdir():
            if plugin_path.is_dir() and plugin_path.name not in loaded_plugins:
                self._load_plugin(plugin_path)
                
    def _load_plugin(self, plugin_path: Path):
        """Загружает отдельный плагин"""
        plugin_name = plugin_path.name
        config_file = plugin_path / "plugin.cfg"
        
        if not config_file.exists():
            print(f"Плагин {plugin_name}: файл plugin.cfg не найден")
            return
            
        try:
            # Читаем конфигурацию плагина
            plugin_info = {}
            
            # Читаем файл построчно, так как он может не иметь секций
            with open(config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        plugin_info[key.strip()] = value.strip()
            
            main_file = plugin_info.get('plugin_main_file', f"{plugin_name.lower()}.py")
            main_file_path = plugin_path / main_file
            
            if not main_file_path.exists():
                print(f"Плагин {plugin_name}: основной файл {main_file} не найден")
                return
                
            # Загружаем модуль плагина
            spec = importlib.util.spec_from_file_location(
                f"plugin_{plugin_name}", 
                main_file_path
            )
            module = importlib.util.module_from_spec(spec)
            
            # Добавляем путь плагина в sys.path для импортов
            plugin_path_str = str(plugin_path)
            if plugin_path_str not in sys.path:
                sys.path.insert(0, plugin_path_str)
                
            spec.loader.exec_module(module)
            
            # Ищем функцию регистрации маршрутов
            register_function_name = f"register_{plugin_name.lower().replace('-', '_')}_routes"
            if hasattr(module, register_function_name):
                register_function = getattr(module, register_function_name)
                
                # Для GitHub плагина, мы не хотим, чтобы он регистрировал catch-all маршрут, если есть другие
                # Вместо этого, мы дадим ему возможность зарегистрировать его, но будем логировать конфликты
                if plugin_name == "GitHub":
                    # GitHub плагин будет вызван последним в load_plugins, поэтому он сможет увидеть все маршруты
                    # Мы не будем здесь перехватывать add_api_route, так как GitHub плагин сам проверяет конфликты
                    register_function(self.app)
                    print(f"Плагин {plugin_name} успешно загружен и зарегистрирован")
                else:
                    # Для других плагинов, регистрируем маршруты как обычно
                    register_function(self.app)
                    print(f"Плагин {plugin_name} успешно загружен и зарегистрирован")

            else:
                print(f"Плагин {plugin_name}: функция {register_function_name} не найдена")
                
            # Сохраняем информацию о плагине
            self.plugins[plugin_name] = {
                'info': plugin_info,
                'module': module,
                'path': plugin_path
            }
            
        except Exception as e:
            print(f"Ошибка при загрузке плагина {plugin_name}: {str(e)}")
            
    def get_plugin_info(self, plugin_name):
        """Возвращает информацию о плагине"""
        return self.plugins.get(plugin_name, {}).get('info', {})
        
    def list_plugins(self):
        """Возвращает список всех загруженных плагинов"""
        return list(self.plugins.keys())

# Создаем основное приложение FastAPI
app = FastAPI(title="ILLUSION CDN Server", description="CDN server with dynamic plugin system")

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Создаем менеджер плагинов
plugin_manager = PluginManager()

# Добавляем маршрут для информации о плагинах
@app.get("/plugins")
async def get_plugins_info():
    """Возвращает информацию о всех загруженных плагинах"""
    plugins_info = {}
    for plugin_name in plugin_manager.list_plugins():
        plugins_info[plugin_name] = plugin_manager.get_plugin_info(plugin_name)
    return plugins_info

# Добавляем маршрут для корневого URL
@app.get("/", response_class=HTMLResponse)
async def home():
    plugins_list = plugin_manager.list_plugins()
    plugins_html = ""
    if plugins_list:
        plugins_html = "<h2>Загруженные плагины:</h2><ul>"
        for plugin in plugins_list:
            plugin_info = plugin_manager.get_plugin_info(plugin)
            description = plugin_info.get('plugin_description', 'Описание отсутствует')
            plugins_html += f"<li><strong>{plugin}</strong> - {description}</li>"
        plugins_html += "</ul>"
    
    return f"""
    <html>
        <head>
            <title>ILLUSION CDN Server</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    line-height: 1.6;
                }}
                h1 {{
                    color: #333;
                }}
                .info {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 20px 0;
                }}
                code {{
                    background-color: #eee;
                    padding: 2px 5px;
                    border-radius: 3px;
                }}
                ul {{
                    margin: 10px 0;
                }}
                li {{
                    margin: 5px 0;
                }}
            </style>
        </head>
        <body>
            <h1>ILLUSION CDN Server с системой плагинов</h1>
            <div class="info">
                <p>Сервер запущен и готов к работе!</p>
                {plugins_html}
                <p>Свяжитесь с нами в Discord:</p>
                <p><code>https://discord.gg/illussion</code></p>
                <p>Свяжитесь с нами в Telegram:</p>
                <p><code>https://t.me/illussion_cdn</code></p>
            </div>
        </body>
    </html>
    """

# Функция запуска сервера
def start_server():
    """Запускает сервер с загрузкой плагинов"""
    print("Запуск ILLUSION CDN Server...")
    print("Загрузка плагинов...")
    
    # Загружаем все плагины
    plugin_manager.load_plugins(app)
    
    print(f"Загружено плагинов: {len(plugin_manager.list_plugins())}")
    for plugin in plugin_manager.list_plugins():
        info = plugin_manager.get_plugin_info(plugin)
        print(f"  - {plugin} v{info.get('plugin_version', 'unknown')}")
    
    print("Сервер готов к запуску!")
    return app

if __name__ == '__main__':
    # Запускаем сервер
    server_app = start_server()
    uvicorn.run(server_app, host="0.0.0.0", port=8000)


