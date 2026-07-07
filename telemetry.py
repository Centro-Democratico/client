import json, os, threading, time
import tkinter as tk
import keyboard, mss, psutil, requests, wmi
from datetime import datetime, timezone

# --- CONFIGURACIÓN DE ENTORNO ---
CONFIG_FILE = "config.json"

def loadConfig():
    """Carga de forma segura la persistencia local del atajo de teclado."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return None

def saveConfig(hotkey):
    """Persiste la preferencia del usuario para evitar re-configuraciones."""
    with open(CONFIG_FILE, "w") as f:
        json.dump({"hotkey": hotkey}, f)


# --- INTERFAZ DE USUARIO: CONFIGURACIÓN ---
class SetupWindow:
    """
    Ventana de configuración inicial encargada de capturar las preferencias
    de entrada por hardware (Key-binding) antes de inicializar la app principal.
    """
    def __init__(self, root):
        self.root = root
        self.root.title("Configuración Inicial")
        
        # Dimensionamiento estático y centrado dinámico en pantalla
        win_width = 320
        win_height = 175
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x_pos = int((screen_width - win_width) / 2)
        y_pos = int((screen_height - win_height) / 2)
        
        self.root.geometry(f"{win_width}x{win_height}+{x_pos}+{y_pos}")
        self.root.resizable(False, False)

        self.hotkey_var = tk.StringVar(value="ctrl+alt+f")

        tk.Label(self.root, text="Atajo para mostrar/ocultar el OSD:", font=("Arial", 10)).pack(pady=(15, 5))
        
        self.entry_hotkey = tk.Entry(self.root, textvariable=self.hotkey_var, font=("Consolas", 12, "bold"), justify="center", state="readonly")
        self.entry_hotkey.pack(pady=5)
        
        self.btn_record = tk.Button(self.root, text="Grabar combinación", command=self.recordHotkey, bg="#444444", fg="white", width=20)
        self.btn_record.pack(pady=5)

        tk.Button(self.root, text="Guardar y Continuar", command=self.saveAndClose, bg="#00A2FF", fg="white", width=20).pack(pady=(5, 0))

    def recordHotkey(self):
        """Asigna un hilo secundario para escuchar el teclado sin congelar la UI de Tkinter."""
        self.btn_record.config(text="Escuchando teclas...", state="disabled", bg="#FFA500")
        self.root.update()
        
        def listen():
            # Bloquea la escucha hasta que se detecte una combinación válida
            new_hotkey = keyboard.read_hotkey(suppress=False)
            # Retorna el control al hilo principal de la UI de forma segura mediante .after()
            self.root.after(0, self.finishRecording, new_hotkey)
            
        threading.Thread(target=listen, daemon=True).start()

    def finishRecording(self, new_hotkey):
        """Callback del hilo de escucha para restaurar el estado de la UI."""
        self.hotkey_var.set(new_hotkey)
        self.btn_record.config(text="Grabar combinación", state="normal", bg="#444444")

    def saveAndClose(self):
        saveConfig(self.hotkey_var.get())
        self.root.destroy()


# --- APLICACIÓN PRINCIPAL: TELEMETRÍA ---
class TelemetryApp:
    """
    Núcleo del sistema. Controla la UI superpuesta (OSD) mediante Tkinter, 
    el bucle de monitoreo asíncrono y el despacho de datos REST hacia el backend.
    """
    def __init__(self, root, hotkey):
        self.root = root
        self.hotkey = hotkey
        
        # Geometría compacta tipo widget de escritorio
        self.win_width = 170
        self.win_height = 105
        
        self.root.geometry(f"{self.win_width}x{self.win_height}+0+0")
        
        # Propiedades de OSD (Sin bordes, siempre visible y con transparencia)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.85)
        
        bg_color = "#111111"
        self.root.configure(bg=bg_color)

        # Estados de control interno
        self.is_visible = True
        self.is_recording = False
        self.session_fps = []
        self.start_time_iso = None

        # Caché de especificaciones físicas del hardware para optimizar llamadas API
        self.hardware_specs = self.getHardwareSpecs()

        # Configuración de malla responsiva para los labels técnicos
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

        tk.Label(self.root, text="CPU", font=("Consolas", 13, "bold"), fg="#00A2FF", bg=bg_color).grid(row=0, column=0, padx=(10, 0), sticky="w")
        self.lbl_cpu = tk.Label(self.root, text="-- %", font=("Consolas", 13, "bold"), fg="#FFA500", bg=bg_color)
        self.lbl_cpu.grid(row=0, column=1, padx=(0, 10), sticky="e")

        tk.Label(self.root, text="RAM", font=("Consolas", 13, "bold"), fg="#00FF00", bg=bg_color).grid(row=1, column=0, padx=(10, 0), sticky="w")
        self.lbl_ram = tk.Label(self.root, text="-- %", font=("Consolas", 13, "bold"), fg="#FFA500", bg=bg_color)
        self.lbl_ram.grid(row=1, column=1, padx=(0, 10), sticky="e")

        tk.Label(self.root, text="FPS", font=("Consolas", 13, "bold"), fg="#FFB6C1", bg=bg_color).grid(row=2, column=0, padx=(10, 0), sticky="w")
        self.lbl_fps = tk.Label(self.root, text="--", font=("Consolas", 13, "bold"), fg="white", bg=bg_color)
        self.lbl_fps.grid(row=2, column=1, padx=(0, 10), sticky="e")

        # Contenedor inferior para los controles de sesión de benchmarking
        frame_btn = tk.Frame(self.root, bg=bg_color)
        frame_btn.grid(row=3, column=0, columnspan=2, pady=(5, 0))

        self.btn_start = tk.Button(frame_btn, text="REC", command=self.startSession, bg="#222222", fg="#00FF00", font=("Consolas", 8), bd=0, width=5)
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = tk.Button(frame_btn, text="STOP", command=self.stopSession, bg="#222222", fg="#FF0000", font=("Consolas", 8), bd=0, width=5, state="disabled")
        self.btn_stop.pack(side="right", padx=5)

        # Gestión del ciclo de vida: Intercepta el cierre forzado para liberar recursos del S.O.
        self.root.protocol("WM_DELETE_WINDOW", self.onClose)

        # Aislamiento del hilo de muestreo hardware para prevenir cuellos de botella en la UI
        self.monitor_thread = threading.Thread(target=self.monitorLoop, daemon=True)
        self.monitor_thread.start()
        
        self.setupHotkeys()

    def getHardwareSpecs(self):
        """Consulta el kernel del sistema operativo mediante WMI y psutil para construir el perfil de hardware."""
        specs = {"cpu": "Desconocido", "gpu": "Desconocido", "ram_gb": 0}
        try:
            c = wmi.WMI()
            specs["cpu"] = c.Win32_Processor()[0].Name.strip()
            specs["gpu"] = c.Win32_VideoController()[0].Name.strip()
            
            # Conversión matemática limpia de bytes a formato legible Gigabytes (GB)
            ram_bytes = psutil.virtual_memory().total
            specs["ram_gb"] = round(ram_bytes / (1024 ** 3))
            print(f"Hardware detectado: {specs['cpu']} | {specs['gpu']} | {specs['ram_gb']}GB RAM")
        except Exception as e:
            print(f"No se pudo detectar el hardware completo: {e}")
        return specs
    
    def setupHotkeys(self):
        """Registra el gancho (hook) global del teclado administrado por el OS."""
        keyboard.add_hotkey(self.hotkey, lambda: self.root.after(0, self.toggleVisibility))

    def toggleVisibility(self):
        """Maneja la visibilidad y estados de foco de la ventana OSD sin destruirla."""
        if self.is_visible:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        self.is_visible = not self.is_visible

    def startSession(self):
        """Inicializa una nueva serie temporal de captura de métricas."""
        self.session_fps = []
        # Estándar de tiempo UTC ISO 8601 exigido en integraciones de bases de datos
        self.start_time_iso = datetime.now(timezone.utc).isoformat()
        self.is_recording = True
        self.btn_start.config(state="disabled", fg="#555555")
        self.btn_stop.config(state="normal", fg="#FF0000")
        self.lbl_fps.config(fg="#FF0000") 

    def stopSession(self):
        """Cierra el flujo de captura actual e inicia el procesamiento asíncrono de carga."""
        self.is_recording = False
        self.btn_start.config(state="normal", fg="#00FF00")
        self.btn_stop.config(state="disabled", fg="#555555")
        self.lbl_fps.config(fg="white") 
        self.processAndSendData()

    def onClose(self):
        """Garantiza la liberación segura de los ganchos del kernel al cerrar el software."""
        keyboard.unhook_all()
        self.root.destroy()

    def processAndSendData(self):
        """
        Reduce y procesa matemáticamente el arreglo de muestras (Time-series data), 
        lo mapea al contrato de la API REST de Django y lo despacha vía HTTP POST.
        """
        if not self.session_fps:
            return
            
        # Extracción analítica mediante comprensiones de listas (List Comprehensions)
        fps_list = [s["fps"] for s in self.session_fps]
        cpu_list = [s["cpu_usage"] for s in self.session_fps]
        ram_list = [s["ram_usage_gb"] for s in self.session_fps]

        fps_avg = sum(fps_list) / len(fps_list) if fps_list else 0

        # Construcción del Payload bajo el principio de consistencia de tipos
        payload = {
            "started_at": self.start_time_iso,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "cpu_avg": round(sum(cpu_list) / len(cpu_list), 2) if cpu_list else 0,
            "cpu_max": max(cpu_list) if cpu_list else 0,
            "gpu_avg": 0, 
            "gpu_max": 0,
            "ram_avg_gb": round(sum(ram_list) / len(ram_list), 2) if ram_list else 0,
            "ram_max_gb": max(ram_list) if ram_list else 0,
            "score": round(fps_avg, 2)  
        }
        
        url_django = "http://127.0.0.1:8000/telemetry/"
        
        try:
            print("Enviando telemetría al servidor principal...")
            respuesta = requests.post(url_django, json=payload, timeout=5)
            
            # Control estructural de códigos de estado HTTP (201 Created)
            if respuesta.status_code == 201:
                print("¡Éxito! Sesión registrada en la base de datos global.")
                try:
                    data = respuesta.json()
                    print(f"ID asignado por el servidor: {data.get('id', 'Generado (UUID)')}")
                except Exception:
                    pass
            else:
                print(f"Falló el envío. Código {respuesta.status_code}: {respuesta.text}")
                
        except requests.exceptions.ConnectionError:
            print("Error: No se pudo conectar. ¿El servidor de Django está encendido?")
        except Exception as e:
            print(f"Ocurrió un error inesperado: {e}")

    def monitorLoop(self):
        """
        Bucle de ejecución infinita (Worker). Calcula los FPS reales mediante 
        conteo de captura de búfer de pantalla (MSS) por segundo y extrae métricas del OS.
        """
        sct = mss.MSS()
        # Fallback estructural para configuraciones multimonitor de Windows
        try: 
            monitor = sct.monitors[1]
        except Exception: 
            monitor = sct.monitors[0]
            
        while True:
            start_time = time.time()
            frames = 0
            
            # Muestreo matemático exacto: Cuenta cuántos barridos de pantalla ocurren en 1.0 segundos reales
            while time.time() - start_time < 1.0:
                sct.grab(monitor)
                frames += 1
                
            cpu_usage = int(psutil.cpu_percent())
            ram_percent = int(psutil.virtual_memory().percent)
            ram_gb = round(psutil.virtual_memory().used / (1024 ** 3), 2)
            
            # Si la sesión de grabación está activa, apendiza la muestra cronológica
            if self.is_recording:
                self.session_fps.append({
                    "fps": frames,
                    "cpu_usage": cpu_usage,
                    "ram_usage_gb": ram_gb,
                    "gpu_usage": 0 
                })
                
            # Despacha los hilos de renderizado de vuelta a Tkinter de forma asíncrona
            self.root.after(0, self.updateLabels, frames, cpu_usage, ram_percent)

    def updateLabels(self, fps, cpu, ram):
        """Actualización directa de elementos atómicos de renderizado de la UI."""
        self.lbl_fps.config(text=f"{fps}")
        self.lbl_cpu.config(text=f"{cpu} %")
        self.lbl_ram.config(text=f"{ram} %")


# --- PUNTO DE ENTRADA DEL SISTEMA ---
if __name__ == "__main__":
    user_config = loadConfig()
    
    # Orquestador lógico: Lanza la configuración si no detecta una previa persistida
    if not user_config:
        setup_root = tk.Tk()
        app_setup = SetupWindow(setup_root)
        setup_root.mainloop()
        
        # Validación de seguridad por si el usuario cierra el setup abruptamente
        user_config = loadConfig()
        if not user_config:
            exit()
            
    # Inicialización del entorno de ejecución de la App Principal
    main_root = tk.Tk()
    app = TelemetryApp(main_root, user_config["hotkey"])
    main_root.mainloop()