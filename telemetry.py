import time
import psutil
import mss

def start_monitoring():
    print("Iniciando monitor de rendimiento...")
    print("Presiona Ctrl+C para detener.\n")
    
    # Inicializamos el motor de captura de pantalla
    sct = mss.mss()
    monitor = sct.monitors[1] # Selecciona tu monitor principal
    
    try:
        while True:
            start_time = time.time()
            frames = 0
            
            # Contamos cuántas capturas podemos hacer en 1 segundo
            while time.time() - start_time < 1.0:
                sct.grab(monitor)
                frames += 1
            
            cpu_usage = psutil.cpu_percent()
            ram_usage = psutil.virtual_memory().percent
            
            current_fps = frames
            
            print(f"Captura -> FPS Visuales: {current_fps} | CPU: {cpu_usage}% | RAM: {ram_usage}%")
            

    except KeyboardInterrupt:
        print("\nMonitor detenido por el usuario.")

if __name__ == "__main__":
    start_monitoring()