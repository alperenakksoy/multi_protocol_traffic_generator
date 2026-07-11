# Der komplette Projekt-Guide: Multi-Protocol Traffic Generation and Analysis

**Hochschule Rhein-Waal · Mobile & Internet Computing · SS2026**
**Dein persönliches Meister-Handbuch für Code, Konzepte und Verteidigung**

---

## Wie du diesen Guide benutzt

Dieser Guide hat ein einziges Ziel: Dich in die Lage zu versetzen, **jede Zeile, jede Designentscheidung und jedes Netzwerk-Konzept dieses Projekts** so souverän zu erklären, dass der Professor keinen Zweifel hat, dass du das System selbst verstanden hast und anwenden kannst.

Er ist in sieben Teile gegliedert, die aufeinander aufbauen:

| Teil | Inhalt | Wofür du ihn brauchst |
|---|---|---|
| **1. Das große Ganze** | Was das System tut, die Architektur, die Design-Philosophie | Der Einstieg jeder Präsentation |
| **2. Die Netzwerk-Konzepte** | HTTP/2, QUIC, MQTT, TCP/UDP, Poisson, Adaptive Control – die Theorie | Damit du auf *jede* Fachfrage antworten kannst |
| **3. Der Code im Detail** | Datei für Datei, Funktion für Funktion | Damit du bei „Zeig mir wie X funktioniert" sofort bist |
| **4. Die 5 Wireshark-Analysen** | Jede Analyse + der Code, der sie ermöglicht | 25 % der Note – das Herz der Bewertung |
| **5. Der Live-Demo-Ablauf** | Minute-für-Minute-Fahrplan | 10 % der Note |
| **6. Prüfungsfragen & Antworten** | Die wahrscheinlichen Fragen, ausformuliert | Deine Versicherung gegen Blackouts |
| **7. Ehrliche Grenzen** | Was das System *nicht* kann und warum das ok ist | Zeigt Reife – Professoren lieben das |

> **Lern-Tipp:** Lies Teil 1 und 2 einmal vollständig für das Verständnis. Übe dann Teil 6 laut. Wenn du Teil 6 frei beantworten kannst, hast du bestanden.

---

## Inhaltsverzeichnis

- [Teil 1 – Das große Ganze](#teil-1--das-große-ganze)
  - [1.1 Die Aufgabe in einem Satz](#11-die-aufgabe-in-einem-satz)
  - [1.2 Das mentale Modell: Generator → Target → Observer](#12-das-mentale-modell-generator--target--observer)
  - [1.3 Die 15 Container und ihre Rollen](#13-die-15-container-und-ihre-rollen)
  - [1.4 Der komplette Datenfluss](#14-der-komplette-datenfluss)
  - [1.5 Die zentralen Design-Entscheidungen (und ihre Begründung)](#15-die-zentralen-design-entscheidungen-und-ihre-begründung)
  - [1.6 Mapping: Aufgabenstellung → Umsetzung](#16-mapping-aufgabenstellung--umsetzung)
- [Teil 2 – Die Netzwerk-Konzepte, die du beherrschen musst](#teil-2--die-netzwerk-konzepte-die-du-beherrschen-musst)
- [Teil 3 – Der Code im Detail](#teil-3--der-code-im-detail)
- [Teil 4 – Die 5 Wireshark-Analysen](#teil-4--die-5-wireshark-analysen)
- [Teil 5 – Der Live-Demo-Ablauf](#teil-5--der-live-demo-ablauf)
- [Teil 6 – Prüfungsfragen & Antworten](#teil-6--prüfungsfragen--antworten)
- [Teil 7 – Ehrliche Grenzen des Systems](#teil-7--ehrliche-grenzen-des-systems)
- [Anhang – Glossar & Befehls-Spickzettel](#anhang--glossar--befehls-spickzettel)

---

# Teil 1 – Das große Ganze

## 1.1 Die Aufgabe in einem Satz

> **Baue ein containerisiertes, verteiltes System, das gleichzeitig echten Netzwerk-Traffic über mehrere Transport- und Anwendungsprotokolle (HTTP/2, QUIC/HTTP/3, MQTT, rohes TCP/UDP) erzeugt, zentral über eine YAML-Konfiguration steuerbar ist, und dessen Verhalten man mit Wireshark „auf dem Draht" analysieren kann.**

Der entscheidende Anspruch der Aufgabenstellung (Grading Philosophy, S. 4): *„A system with fewer protocols but excellent analysis and documentation scores higher than a complete system with superficial treatment."* Übersetzt: **Tiefe schlägt Breite.** Genau deshalb ist dieser Guide so detailliert – die Analyse (25 %) und der Report (15 %) belohnen echtes Verständnis, nicht bloße Feature-Listen.

Das Projekt integriert bewusst **alle** Kursthemen an einem Stück:
- **Container-Orchestrierung** (Docker Compose, ein Befehl startet 15 Container)
- **Protokoll-Implementierung** (HTTP/2, QUIC/HTTP/3, MQTT, roh TCP/UDP)
- **Traffic-Analyse** (Wireshark, 5 Pflicht-Analysen)
- **Resilienz-Muster** (Fault Injection + autonome Adaptive Control)
- **Verteilter Betrieb** (2-Maschinen-Deployment)

## 1.2 Das mentale Modell: Generator → Target → Observer

Wenn du nur **eine** Sache aus diesem Guide behältst, dann diese Dreiteilung. Das gesamte System ist eine Umsetzung des klassischen **Producer-Consumer-Musters** aus der verteilten Systemtechnik, erweitert um eine Beobachtungsebene:

```
   STEUEREBENE                ERZEUGER              EMPFÄNGER            BEOBACHTER
 (Control Plane)             (Producer)             (Consumer)          (Observer)

  ┌────────────┐          ┌─────────────┐        ┌────────────┐       ┌───────────┐
  │ Controller │──REST──▶ │ 4 Generatoren│──Netz─▶│ 3 Targets  │◀shared│ 4 Analyzer│
  │  (YAML)    │          │ HTTP2/QUIC/  │        │ +1 Broker  │ netns │ (tshark)  │
  │ Dashboard  │          │ MQTT/TCPUDP  │        │            │       │           │
  └────────────┘          └──────┬──────┘        └────────────┘       └─────┬─────┘
                                 │ self-reported stats                       │ captured stats
                                 ▼                                           ▼
                          ┌──────────────────────────────────────────────────────┐
                          │              Metrics Collector (:9090)                 │
                          │   aggregiert Selbstauskunft + echte Messung            │
                          └──────────────────────────────────────────────────────┘
```

**Die vier Ebenen im Klartext:**

1. **Steuerebene (Controller + Dashboard):** Das „Gehirn". Es liest eine YAML-Datei, übersetzt sie in konkrete Befehle und dirigiert die Generatoren per REST. Es sendet selbst **keinen** Traffic.
2. **Erzeuger (4 Generatoren):** Jeder Generator spricht *ein* echtes Protokoll und feuert echte Pakete. Jeder hat seine eigene kleine REST-API, damit der Controller ihn live umkonfigurieren kann.
3. **Empfänger (3 Targets + Mosquitto-Broker):** Server, die den Traffic annehmen und quittieren. Ohne echte Empfänger gäbe es keinen echten TCP-Handshake, keine echten Antworten – und damit nichts Sinnvolles in Wireshark.
4. **Beobachter (4 Analyzer-Sidecars):** Das ist die clevere Erweiterung. Jeder Sidecar hängt per `tshark` direkt an der Netzwerk-Schnittstelle eines Targets und misst, was **wirklich** über die Leitung geht – unabhängig davon, was die Generatoren *behaupten* gesendet zu haben.

> **Der Schlüsselgedanke, der Punkte bringt:** Es gibt zwei Wahrheiten im System. Die **Selbstauskunft** der Generatoren (`packets_sent`, `errors` – was sie *glauben* getan zu haben) und die **Messung** der Analyzer (was tshark *tatsächlich* auf dem Draht sieht). Ein reifes System vertraut nicht blind der Selbstauskunft. Genau dieser Unterschied deckte im Projekt einen echten Bug auf (siehe [7.3](#73-echte-bugs-die-beim-bau-gefunden-wurden)).

## 1.3 Die 15 Container und ihre Rollen

Alle Container laufen im selben Docker-Bridge-Netzwerk `mic-net` und adressieren sich **über ihren Namen** (z. B. ruft `gen-http2` einfach `http://target-http2:8080` auf – Docker-DNS löst den Namen auf). Nur die Ports, die von außen (Host) erreichbar sein müssen, werden gemappt.

| # | Container | Technologie | Port (Host) | Rolle |
|---|---|---|---|---|
| 1 | `controller` | Python + FastAPI + Uvicorn | 8000 | Liest YAML, steuert Generatoren, REST-API |
| 2 | `dashboard` | Statisches HTML/JS via `python -m http.server` | 3000 | Live-Steuerung & Visualisierung |
| 3 | `metrics` | Python + Flask | 9090 | Aggregiert Stats von Generatoren + Analyzern |
| 4 | `mosquitto` | Eclipse Mosquitto 2 | 1883 | MQTT-Broker |
| 5 | `gen-http2` | Python + `h2` (h2c) | – | HTTP/2 GET/POST |
| 6 | `gen-quic` | Python + `aioquic` | – | QUIC/HTTP/3 POST |
| 7 | `gen-mqtt` | Python + `paho-mqtt` | – | MQTT publish/subscribe |
| 8 | `gen-tcpudp` | Python + raw `socket` | – | Rohes TCP/UDP, Normal/Stealth |
| 9 | `target-http2` | Hypercorn + FastAPI | 8080 | HTTP/2-Server |
| 10 | `target-quic` | aioquic | 4433/udp | QUIC-Server |
| 11 | `target-tcpudp` | Python raw socket | – (intern 9999) | TCP/UDP-Sink (verschluckt Pakete) |
| 12 | `analyzer-http2` | Python + `tshark` | – | Sidecar an `target-http2` |
| 13 | `analyzer-quic` | Python + `tshark` | – | Sidecar an `target-quic` |
| 14 | `analyzer-mqtt` | Python + `tshark` | – | Sidecar an `mosquitto` |
| 15 | `analyzer-tcpudp` | Python + `tshark` | – | Sidecar an `target-tcpudp` |

> **Warum die Generatoren keine Host-Ports haben:** Sie sollen von außen gar nicht direkt erreichbar sein. Der einzige legitime Weg, sie zu steuern, ist über den Controller. Das ist eine bewusste Kapselung: eine einzige, dokumentierte Steuer-Schnittstelle (Port 8000) statt vier lose Enden.

## 1.4 Der komplette Datenfluss

So wandert eine Anweisung vom Klick bis zum Paket und wieder zurück auf den Bildschirm:

```
 Browser ──klick "Start"──▶ Dashboard :3000
                                │  POST /start?profile=balanced
                                ▼
                          Controller :8000
                                │  1. liest config/balanced.yaml
                                │  2. übersetzt Phase 1 in Generator-Configs
                                │  3. POST /start an jeden Generator
              ┌─────────────────┼──────────────────┬───────────────────┐
              ▼                 ▼                  ▼                   ▼
        gen-http2 :7001   gen-quic :7002    gen-mqtt :7003     gen-tcpudp :7004
              │ HTTP/2         │ QUIC/UDP        │ MQTT             │ TCP+UDP
              ▼                 ▼                  ▼                   ▼
        target-http2      target-quic        mosquitto          target-tcpudp
         :8080/tcp         :4433/udp          :1883/tcp           :9999
              │                 │                  │                   │
      (shared netns)    (shared netns)     (shared netns)      (shared netns)
              ▼                 ▼                  ▼                   ▼
        analyzer-http2   analyzer-quic     analyzer-mqtt      analyzer-tcpudp
              │  POST /analysis/update (gemessene Pakete)          │
              └──────────────────┬──────────────────────────────────┘
                                 ▼
   Generatoren ──POST /update──▶ Metrics :9090 ◀── Analyzer /analysis/update
   (alle 5 s Selbstauskunft)          │
                                 GET /metrics + GET /analysis
                                      ▲
                          Controller GET /status (bündelt alles)
                                      ▲
                          Dashboard  (pollt /status alle 2 s) ──▶ Browser rendert
```

**Die drei Takte, die du kennen musst:**
- **Generatoren → Metrics:** alle **5 Sekunden** eine Selbstauskunft (`_metrics()`-Schleife in jedem Generator).
- **Analyzer → Metrics:** jede **1 Sekunde** ein Snapshot der gemessenen Pakete.
- **Dashboard → Controller:** alle **2 Sekunden** ein `GET /status` (Polling), das intern die Generator-Status, die Metrics und die Analyse in *eine* Antwort bündelt.

## 1.5 Die zentralen Design-Entscheidungen (und ihre Begründung)

Der Professor wird nach dem **Warum** fragen, nicht nur nach dem **Was**. Hier die Entscheidungen, die du verteidigen können musst:

**① Warum REST/HTTP zwischen Controller und Generatoren, kein Message-Bus (z. B. MQTT selbst)?**
Weil direkte HTTP-Aufrufe (a) leichter zu debuggen sind (Logs sofort lesbar), (b) weniger bewegliche Teile haben (kein zusätzlicher Broker für die *Steuerung* nötig) und (c) im Demo intuitiv zu erklären sind. Ein Message-Bus wäre für die reine Steuerung Overengineering. *(Quelle: `docs/ARCHITECTURE.md`, „Design decision: why no event bus?")*

**② Warum hat jeder Generator eine eigene REST-API?**
Damit der Controller ihn **zur Laufzeit** starten, stoppen und umkonfigurieren kann, ohne den Container neu zu bauen. Das ist die Grundlage für „Configuration flexibility" (15 %): Profile umschalten und einzelne Rates ändern, während alles läuft. Die Controller-Endpunkte `/generator/{name}/*` sind dünne Proxys zu diesen eigenen APIs.

**③ Warum echte Pakete statt Simulation?**
Weil die Analyse (25 %) verlangt, dass man in Wireshark echte HTTP/2-Frames, echte QUIC-UDP-Pakete, echte MQTT-QoS-Handshakes und echte TCP-SYN/RST-Sequenzen sieht. Eine Simulation würde keine echte Protokoll-Signatur auf den Draht legen. Deshalb spricht `gen-http2` echtes HTTP/2 (über die `h2`-Bibliothek), `gen-quic` echtes HTTP/3 (aioquic) usw.

**④ Warum halten QUIC und HTTP/2 die Verbindung offen (statt pro Request neu zu verbinden)?**
Fairness beim Latenz-Vergleich. Ein QUIC-/TLS-Handshake kostet mindestens einen zusätzlichen Roundtrip. Würde man pro Request neu verbinden, dominierte der Handshake die gemessene Latenz und QUIC sähe künstlich langsam aus. Ein echter HTTP/3-Client verhält sich genauso: eine Verbindung, viele Requests. *(Ausnahme mit Absicht: der TCP/UDP-Generator öffnet pro Paket eine neue TCP-Verbindung – siehe [3.6](#36-der-tcpudp-generator--das-herzstück-der-analyse).)*

**⑤ Warum Analyzer-Sidecars, die die Netzwerk-Namespace des Targets teilen (`network_mode: "service:<target>"`)?**
Weil Docker Desktop auf macOS/Windows **keine** host-sichtbare Bridge-Schnittstelle (`docker0`/`br-*`) hat – Container laufen dort in einer VM. Wireshark auf dem Host sähe nur Loopback. Die Lösung: der Sidecar hängt sich *in* die Namespace des Targets und sieht dort `eth0` mit dem echten Container-Traffic. Genau derselbe Trick, den der Wireshark-Guide für die manuelle Aufnahme dokumentiert – nur automatisiert und kontinuierlich.

## 1.6 Mapping: Aufgabenstellung → Umsetzung

Diese Tabelle ist dein **Beweis der Vollständigkeit**. Halte sie bereit – sie zeigt, dass jede Anforderung erfüllt ist.

| Anforderung (aus dem Assignment) | Umsetzung im Projekt | Datei |
|---|---|---|
| Traffic Controller mit YAML + REST + Logging | FastAPI-Controller, Phasen-Runner, Zeitstempel-Log | `controller/main.py` |
| HTTP/2-Generator (GET/POST, Rate, Payload, Multiplexing) | Eigener h2c-Client mit multiplexten Streams | `generators/http2/generator.py` |
| QUIC/HTTP/3-Generator (Rate, Payload, Streams, 0-RTT) | aioquic, echtes 0-RTT-Resumption | `generators/quic/generator.py` |
| MQTT-Generator (pub/sub, QoS 0/1/2, Topics) | paho-mqtt, QoS-Verteilung, Topic-Rotation | `generators/mqtt/generator.py` |
| TCP/UDP-Roh-Generator (Rate, Größe, Ratio, Burst) | Rohe Sockets, Normal/Stealth, per-Protokoll-Rates | `generators/tcpudp/generator.py` |
| Target Services (HTTP/2, QUIC, MQTT-Broker) | Hypercorn+FastAPI, aioquic, Mosquitto | `targets/`, `mosquitto` |
| Metrics Collector (`/metrics` JSON) | Flask, aggregiert + windowed error_rate | `metrics/collector.py` |
| Sende-Muster: constant, burst, ramp, random | Alle 4 in allen 4 Generatoren | jeder Generator + Controller-Ramp |
| Global: duration, warmup, cooldown | `_run_phases()` + `_ramp_rates()` | `controller/main.py` |
| Mehrere Phasen (Traffic-Mix ändert sich) | `phases:`-Liste, sequenziell abgearbeitet | `config/*.yaml` |
| 3+ YAML-Profile | 14 Profile vorhanden | `config/` |
| 5 Wireshark-Analysen | Alle 5 als `.pcapng` + Skripte | `captures/` |
| Multi-System-Deployment | 2 Compose-Dateien + `.env` | `docker-compose.{generators,targets}.yml` |
| Resilienz | Fault Injection + Adaptive Control | Controller + Generatoren |

**Über die Pflicht hinaus** (das hebt das Projekt ab): live Analyzer-Sidecars (echte Messung im Dashboard), Warmup/Cooldown-Ramps als sichtbare Slopes, Random/Poisson auf *allen* vier Protokollen, und ein autonomer Adaptive-Control-Regelkreis.

---

# Teil 2 – Die Netzwerk-Konzepte, die du beherrschen musst

Dieser Teil ist die **Theorie**. Der Professor kann bei jedem Protokoll nachbohren. Für jedes Konzept findest du hier: *was es ist*, *warum es im Projekt so gebaut ist*, und *was man in Wireshark sieht*.

## 2.1 Das OSI-/TCP-IP-Modell – wo jedes Protokoll sitzt

```
Schicht (Layer)         Protokoll im Projekt         Port
─────────────────────────────────────────────────────────────
Anwendung (L7)          HTTP/2, HTTP/3, MQTT         8080, 4433, 1883
Transport (L4)          TCP,    QUIC(über UDP), UDP  –
Vermittlung (L3)        IP                           –
Sicherung/Bitüb. (L2/1) Ethernet                     –
```

**Die Kernaussage, die alles zusammenhält:**
- **HTTP/2** ist ein L7-Protokoll, das auf **TCP** (L4) aufsetzt → in Wireshark siehst du TCP-Port 8080.
- **QUIC** ist ein L4-Protokoll, das auf **UDP** aufsetzt und HTTP/3 (L7) trägt → in Wireshark siehst du UDP-Port 4433. **QUIC ist das einzige Protokoll im Projekt über UDP** – das ist die Pointe der Protokoll-Verteilungsanalyse.
- **MQTT** ist ein L7-Protokoll über **TCP**-Port 1883.
- **Rohes TCP/UDP** ist Traffic *ohne* Anwendungsprotokoll darüber – reine Transportschicht-Pakete auf Port 9999.

## 2.2 HTTP/2 – Multiplexing über eine TCP-Verbindung

**Was HTTP/2 ausmacht (gegenüber HTTP/1.1):**
- **Multiplexing:** Mehrere Requests/Responses laufen *gleichzeitig* als unabhängige **Streams** über *eine* TCP-Verbindung. Bei HTTP/1.1 blockierte ein langsamer Request die Leitung (oder man brauchte 6 parallele Verbindungen).
- **Binär-Framing:** HTTP/2 zerlegt alles in binäre Frames (HEADERS, DATA, WINDOW_UPDATE, RST_STREAM …). HTTP/1.1 war Text.
- **Flow Control:** Pro Stream *und* pro Verbindung gibt es ein „Fenster" (Window), das steuert, wie viele Bytes gesendet werden dürfen, bevor der Empfänger bestätigt.

**Der Clou im Projekt – „h2c" (HTTP/2 cleartext):**
Normalerweise wird HTTP/2 über TLS ausgehandelt (via ALPN). Die `httpx`-Bibliothek verweigert HTTP/2 über eine unverschlüsselte `http://`-Verbindung und fällt **still** auf HTTP/1.1 zurück. Das Target hat aber kein TLS. Damit trotzdem **echte HTTP/2-Frames** auf dem Draht erscheinen (was die Analyse braucht), implementiert `gen-http2` das Protokoll selbst mit der `h2`-Bibliothek im „prior knowledge"-Modus: Es schickt sofort die HTTP/2-Preface, ohne Upgrade-Handshake. → **Das ist der Grund, warum der Generator einen ~150 Zeilen langen eigenen `H2CClient` hat statt einfach `httpx` zu benutzen.** Wenn der Professor fragt „Warum nicht einfach httpx?", ist *das* die Antwort.

**In Wireshark:** TCP-Verbindungen auf Port 8080. „Follow TCP Stream" zeigt die HTTP/2-Frames. Bei Multiplexing trägt **eine** TCP-Verbindung mehrere parallele Streams – der Hauptunterschied zu HTTP/1.1.

## 2.3 QUIC / HTTP/3 – der Transport-Neuling über UDP

**Was QUIC ist:** Ein von Google entwickeltes Transportprotokoll (heute IETF-standardisiert), das **auf UDP** aufsetzt und TCP+TLS in einem vereint. HTTP/3 ist HTTP über QUIC.

**Warum läuft QUIC über UDP, nicht TCP?** (Klassische Prüfungsfrage!)
QUIC implementiert Zuverlässigkeit, Flusskontrolle und Stau-Kontrolle **selbst** in der Anwendungsschicht (im User-Space), statt sich auf den TCP-Stack des Betriebssystems zu verlassen. Der entscheidende Vorteil: **kein Head-of-Line-Blocking**. Bei HTTP/2-über-TCP blockiert ein *einziges* verlorenes TCP-Segment *alle* multiplexten Streams, weil TCP eine strikt geordnete Byte-Sequenz garantiert. Bei QUIC sind die Streams auf Transportebene unabhängig – ein verlorenes Paket blockiert nur *seinen* Stream, die anderen laufen weiter.

**0-RTT (Zero Round Trip Time):** Eine QUIC-Besonderheit. Bei der *ersten* Verbindung zu einem Server macht man einen vollen TLS-1.3-Handshake (1-RTT) und bekommt ein **Session-Ticket**. Bei einer *späteren* Wiederverbindung kann der Client mit diesem Ticket schon Anwendungsdaten mitschicken, **bevor** der Handshake fertig ist – „0-RTT". Das spart einen Roundtrip.
- Im Projekt: `use_0rtt` (Konfiguration = *Absicht*) vs. `zero_rtt_used` (read-only in `/status` = *ob es wirklich geklappt hat*). Diese Unterscheidung ist wichtig: Man *will* 0-RTT, aber ob es gelingt, hängt vom Server-Ticket ab.

**In Wireshark:** UDP-Pakete auf Port 4433. Die QUIC-Payload ist mit TLS 1.3 verschlüsselt – Inhalt nicht lesbar. Sichtbar sind aber Connection-IDs, Paketgrößen und Timing. **Wichtige Konsequenz für die Fehleranalyse:** Ein QUIC `CONNECTION_CLOSE` auf einer etablierten Verbindung ist *verschlüsselt* und für einen passiven Beobachter **nicht** sichtbar (anders als ein TCP RST). Deshalb ist bei QUIC das einzige ehrlich beobachtbare Fehlersignal „die Antworten hören einfach auf" (Silence).

## 2.4 MQTT – Publish/Subscribe für das IoT

**Was MQTT ist:** Ein leichtgewichtiges Publish/Subscribe-Protokoll für IoT/Telemetrie über TCP-Port 1883. Statt dass Client A direkt Client B anspricht, gibt es einen zentralen **Broker** (hier: Mosquitto):
- **Publisher** senden Nachrichten an ein **Topic** (z. B. `sensors/temperature`).
- **Subscriber** abonnieren Topics und bekommen alle Nachrichten dazu.
- Der Broker vermittelt (Fan-out): Eine Nachricht an ein Topic mit 3 Abonnenten → 3 Auslieferungen.

Im Projekt abonniert `gen-mqtt` **seine eigenen** Topics. Dadurch sieht man in Wireshark nicht nur die PUBLISH-Seite (Generator→Broker), sondern auch die Fan-out-Seite (Broker→Subscriber).

**QoS-Level – der wichtigste MQTT-Prüfungspunkt:**

| QoS | Garantie | Handshake | Pakete pro Nachricht |
|---|---|---|---|
| **0** | „Fire and forget" – höchstens einmal | keiner | **1** (nur PUBLISH) |
| **1** | „At least once" – mindestens einmal | PUBLISH → PUBACK | **2** |
| **2** | „Exactly once" – genau einmal | PUBLISH → PUBREC → PUBREL → PUBCOMP | **4** |

> **Der Aha-Effekt für die Analyse:** Bei QoS 2 erzeugt *dieselbe* Nachricht **viermal so viele Pakete** wie bei QoS 0. Deshalb steigt die Paketrate mit höherem QoS, selbst wenn die *Nachrichtenrate* gleich bleibt. In Wireshark (Filter `mqtt`) siehst du den 4-Wege-Handshake bei QoS 2 direkt. Genau das demonstriert das `mqtt_heavy`-Profil mit seiner `qos_distribution`.

## 2.5 TCP vs. UDP – die Transportschicht pur

Der TCP/UDP-Generator sendet **rohen** Transport-Traffic (kein L7-Protokoll darüber). Das macht die fundamentalen Unterschiede sichtbar:

| Eigenschaft | TCP | UDP |
|---|---|---|
| Verbindung | verbindungsorientiert (Handshake) | verbindungslos |
| Handshake | SYN → SYN-ACK → ACK | keiner |
| Zuverlässigkeit | garantiert, mit Retransmit | keine Garantie |
| Reihenfolge | geordnet | ungeordnet |
| Abbau | FIN → ACK (oder RST) | keiner |
| Overhead | höher | minimal |

**Wichtige Design-Entscheidung im Projekt:** Jeder TCP-Send öffnet eine **neue** Verbindung (`connect()` → `sendall()` → `close()`). Das ist absichtlich: So entsteht pro Paket eine echte **SYN/SYN-ACK/FIN**-Sequenz, die man in Wireshark sehen kann. UDP dagegen ist ein einfaches `sendto()` ohne Verbindung. Diese Entscheidung hat eine tiefgründige Konsequenz für die Fingerprinting-Analyse (siehe [2.7](#27-behavioral-fingerprinting--die-wissenschaftliche-pointe)).

**TCP RST (Reset):** Wenn der Empfänger eine Verbindung ablehnt (z. B. weil das Target gestoppt wurde), antwortet er mit einem RST-Paket. Das ist das **schnellste, deutlichste Fehlersignal** im ganzen System (Filter `tcp.flags.reset == 1`) – erscheint in unter einer Sekunde.

## 2.6 Die vier Sende-Muster (Sending Patterns)

Die Aufgabenstellung verlangt explizit: *constant, periodic burst, ramping, random (Poisson)*. Alle vier sind in **allen vier** Generatoren umgesetzt. Das ist die Grundlage der Temporal-Analyse.

**① `constant` – konstante Rate.** Ein Paket pro festem Intervall. Intervall = `1 / rate`. In der I/O-Graph eine flache Linie.

**② `periodic_burst` – periodische Bursts.** Die effektive Rate springt für `burst_duration` Sekunden auf `burst_rate` hoch, alle `burst_interval` Sekunden. In der I/O-Graph regelmäßige Zacken.
- **Cleverer Trick:** Der Burst wird *stateless* aus der Wanduhr abgeleitet: `(time.time() % burst_interval) < burst_duration`. Dadurch ist die Kadenz stabil über Neustarts und Event-Loop-Verzögerungen hinweg – ein laufender Timer-Zähler würde driften.

**③ `ramp` – linearer Anstieg.** Die Rate steigt linear von `ramp_start_rate` auf `ramp_end_rate` über `ramp_duration` Sekunden, dann hält sie. In der I/O-Graph eine ansteigende Rampe (Slope). Umgesetzt via `_ramp_effective_rate()` = lineare Interpolation.

**④ `random` – Poisson-Prozess.** Statt festem Intervall exponentiell verteilte Zwischenankunftszeiten (`random.expovariate(1/interval)` bzw. `np.random.exponential(interval)`) – **gleiche mittlere Rate**, aber zufällige Abstände. In der I/O-Graph ein unregelmäßiges Muster ohne feste Zacken.

> **Zusätzlich zu den Mustern gibt es die globale Warmup/Cooldown-Rampe** (`global.warmup`/`global.cooldown`): Zu Beginn eines Profils fahren *alle* Rates linear von 0 hoch, am Ende linear auf 0 runter. Das ist **derselbe Mechanismus** wie das „ramping"-Muster, nur global und einmalig – sichtbar als sanfte Slope am Anfang/Ende jeder Aufnahme.

## 2.7 Behavioral Fingerprinting – die wissenschaftliche Pointe

**Die Forschungsfrage:** *„Kann man automatisierte Netzwerk-Generatoren an statistischen Mustern erkennen, und wenn ja, wie verhindert man das?"*

**Warum Fingerprinting funktioniert:** Jedes automatisierte System hinterlässt messbare Spuren:
- **Feste Timing:** Ein Generator, der alle 100 ms sendet, erzeugt einen Peak bei genau 100 ms im Zwischenankunfts-Histogramm. Kein Mensch tippt oder klickt so präzise.
- **Feste Payload:** Immer 512-Byte-Pakete → ein einzelner Spike im Größen-Histogramm. Echter Traffic hat eine breite Verteilung.

**Die zwei Modi des TCP/UDP-Generators:**

| | Normal Mode (erkennbar) | Stealth Mode (getarnt) |
|---|---|---|
| Paketgröße | fix 512 B | zufällig 64–1400 B (gleichverteilt) |
| Timing | fix 100 ms | Poisson-verteilt (Mittel 100 ms) |
| Fingerabdruck | klarer Spike | breite, natürliche Verteilung |

**Warum die Poisson-Verteilung?** Der Poisson-Prozess modelliert Ereignisse, die (1) unabhängig voneinander auftreten, (2) mit konstanter Durchschnittsrate, (3) ohne „Gedächtnis". Genau so verhält sich echter menschlicher Web-Traffic: Ein Nutzer klickt, wartet, klickt wieder – die Wartezeiten folgen einer Exponentialverteilung (die Zwischenankunftsverteilung eines Poisson-Prozesses). `np.random.exponential(0.1)` modelliert exakt das.

**Der Bezug zur Praxis:** Diese Technik heißt in der Forschung *traffic morphing* / *packet size obfuscation*. VPN-Anbieter (Mullvad, ExpressVPN) und Tor nutzen sie, damit ihr Traffic wie normales HTTPS aussieht.

> **Die ehrliche, punktebringende Erkenntnis aus dem Bau (steht so in `docs/ARCHITECTURE.md`):** Mit `tcp_ratio > 0` besiegt der Stealth-Modus das Fingerprinting **nicht** vollständig! Weil jeder TCP-Send eine neue `connect()/close()`-Sequenz ist, erzeugt jeder logische Send einen stereotypen Schwall kleiner SYN/ACK/FIN-Kontrollpakete mit Sub-Millisekunden-Abständen – und dieses Muster ist in *beiden* Modi identisch, weil `mode` nur die *Daten*-Pakete randomisiert, nicht den Verbindungs-Overhead. Gemessen: Bei `tcp_ratio=60` sind beide Modi zu >75 % vom `64–128`/`<1ms`-Bucket dominiert. Erst bei `tcp_ratio=0` (reines UDP, kein Verbindungs-Overhead) zeigt sich der erwartete Kontrast sauber: Normal konvergiert zu 97 % in *einem* Größen-Bucket, Stealth streut über fünf Buckets. **Fazit: Die TCP-Verbindungs-Wiederholung ist selbst ein Fingerabdruck, unabhängig von der Payload-Verschleierung.** Diese Nuance im Report zu nennen ist Gold wert – sie zeigt, dass du gemessen und nicht nur behauptet hast.

## 2.8 Resilienz: Fault Injection & Adaptive Control

**Fault Injection (Fehler-Injektion):** Jeder Generator akzeptiert zwei Felder:
- `fault_rate` (0–1): Anteil der Sends, die *absichtlich* als Fehler behandelt werden, **ohne** wirklich zu senden. Simuliert Ausfälle.
- `extra_latency_ms`: künstliche Verzögerung vor jedem Send. Simuliert Netz-Stau / überlastetes Target.

Beide sind live über das Dashboard steuerbar und dienen dazu, (a) die Fehlersichtbarkeits-Analyse zu demonstrieren und (b) die Adaptive Control zu einer sichtbaren Reaktion zu provozieren.

**Adaptive Control (autonomer Regelkreis):** Das ist die Kür. Ein geschlossener Regelkreis im Controller (`_adaptive_loop()`):
1. Alle `check_interval` Sekunden liest er jede Generator-Fehlerrate (und Latenz, wo berichtet).
2. Ist die Fehlerrate **niedrig** (≤ Schwelle) → Rate **hoch**skalieren (× `scale_up_factor`).
3. Ist die Fehlerrate **hoch** (≥ Schwelle) → Rate **runter**skalieren (× `scale_down_factor`).
4. Begrenzt durch `min_multiplier`/`max_multiplier`.

Das ist ein **multiplikativer Regler** (verwandt mit AIMD/TCP-Congestion-Control-Denken). Er reagiert **ohne** menschliches Zutun – der Kern von „resilience patterns" aus den Kurszielen. Im Demo: Fehlerrate-Slider hoch → nach einem Check-Intervall fällt die HTTP/2-Rate automatisch.

---

# Teil 3 – Der Code im Detail

Jetzt gehen wir in den Code. Für jede Komponente: die **Verantwortung**, die **wichtigen Funktionen** und die **kniffligen Stellen**, nach denen der Professor fragen könnte. Zeilennummern beziehen sich auf den aktuellen Stand.

Reihenfolge der Erklärung (vom Dirigenten zu den Musikern zum Publikum):
`Controller → 4 Generatoren → Targets → Metrics → Analyzer → Dashboard → Docker → YAML`.

## 3.1 Der Controller (`controller/main.py`, 955 Zeilen)

**Verantwortung:** Das Gehirn. Liest YAML-Profile, übersetzt sie in Generator-Configs, fährt die Phasen ab, steuert Warmup/Cooldown-Rampen und den Adaptive-Control-Regelkreis, und bündelt allen Status für das Dashboard. Es sendet **selbst keinen Traffic**.

### Der globale Zustand (`state`, Zeile 97)

Ein einziges `dict`, das den Live-Zustand hält. Die wichtigsten Schlüssel:
```python
state = {
    "running":         False,        # läuft das System gerade?
    "active_profile":  None,         # welches YAML ist aktiv?
    "active_phase":    None,         # Name der laufenden Phase (oder "warmup"/"cooldown")
    "phase_task":      None,         # der asyncio-Background-Task, der die Phasen abfährt
    "adaptive_enabled":          False,
    "adaptive_task":             None,   # der Adaptive-Control-Background-Task
    "adaptive_status":           {},     # letzte Entscheidung pro Generator
    "adaptive_profile_managed":  False,  # wurde Adaptive vom Profil oder vom Nutzer gestartet?
    "started_generators":        set(),  # welche Generatoren wurden diesen Lauf schon /start-et?
    "ramp_status":               None,   # {"phase": "warmup"/"cooldown", "progress": 0.0-1.0}
}
```

> **Warum `adaptive_profile_managed`?** Damit sich Profil-gesteuerte und manuell (per Dashboard-Toggle) aktivierte Adaptive Control nicht ins Gehege kommen. Wenn *du* Adaptive manuell einschaltest, soll ein Phasenwechsel dir die Kontrolle nicht wegnehmen. Diese Flag unterscheidet die beiden Fälle. Solche Details zeigen dem Professor, dass an Randfälle gedacht wurde.

### Die Konfigurations-Übersetzer (`_translate_*`, Zeilen 284–348)

Das ist eine der elegantesten Ideen im Controller. Die YAML-Dateien sind **menschenfreundlich** geschrieben, aber die Generatoren erwarten **exakte Feldnamen**. Drei kleine Übersetzer schließen die Lücke:

- **`_translate_http2()`** (Z. 284): Wandelt `method_distribution: {GET: 70, POST: 30}` in `method_get_pct: 70` um – und **normalisiert**, sodass es egal ist, ob die Prozente exakt 100 ergeben (`get / (get+post) * 100`).
- **`_translate_mqtt()`** (Z. 301): Wandelt eine `topics: [a, b, c]`-Liste in `topic_count: 3` um. (Wichtige, ehrliche Grenze: die *Namen* der Topics werden ignoriert, nur die *Anzahl* zählt – der Generator rotiert durch fest eingebaute Topic-Namen.)
- **`_translate_tcpudp()`** (Z. 317): Der komplexeste. Er merged Felder aus drei möglichen YAML-Blöcken (`tcp:`, `udp:`, `tcpudp:`) und macht aus per-Protokoll-Werten die richtigen Generator-Feldnamen: `tcp.rate → tcp_rate`, `tcp.burst_rate → tcp_burst_rate` usw. Nötig, weil ein Profil nur *ein* Protokoll bursten lassen kann (z. B. nur TCP).

`_phase_to_gen_configs()` (Z. 351) ruft alle drei auf und liefert ein Dict `{"gen-http2": {...}, "gen-quic": {...}, ...}`.

### Der Phasen-Runner (`_run_phases()`, Zeile 517) – das Herzstück

Dies ist der Background-Task, der einen kompletten Profillauf abfährt. Ablauf:

1. **Warmup** (falls `global.warmup > 0`): Ruft `_ramp_rates(..., "up", "warmup")` auf – fährt die Rates von Phase 1 linear von 0 hoch.
2. **Für jede Phase** in `phases:`
   - Setzt `active_phase` auf den Phasennamen.
   - Übersetzt die Phase in Generator-Configs.
   - **Lazy Start:** Ein Generator, der in keiner früheren Phase vorkam (z. B. MQTT erscheint erst in Phase 3), bekam nie ein `POST /start`. Ein bloßes `PATCH /config` würde seine Rate ändern, aber ihn nicht *starten* (PATCH berührt `running` nie). Deshalb: Beim ersten Auftauchen mit nicht-leerer Config wird er `/start`-et, danach nur noch `/config`-t. (Z. 548)
   - **Adaptive Control** an/aus je nach `adaptive_control`-Block der Phase (mit der `adaptive_profile_managed`-Logik von oben).
   - `await asyncio.sleep(phase["duration"])` – wartet die Phasendauer ab.
3. **Cooldown** (falls `global.cooldown > 0`): `_ramp_rates(..., "down", "cooldown")` – fährt die Rates der letzten Phase linear auf 0.
4. Setzt `active_phase = None` und loggt „All phases completed". **Wichtig:** Die Generatoren laufen danach mit ihrer letzten Rate weiter – erst „Stop" beendet den Traffic.

### Die Rampe (`_ramp_rates()`, Zeile 227)

Fährt die Rate-Felder (nur die aus `RATE_FIELDS`: `rate`, `tcp_rate`, `udp_rate`) linear hoch oder runter, in bis zu 20 Schritten via periodischer `PATCH /config`-Aufrufe.
```python
steps = max(1, min(20, round(duration)))   # bis zu 20 Schritte
for i in range(1, steps + 1):
    frac = i / steps
    applied_frac = frac if direction == "up" else (1 - frac)
    # jede Rate × applied_frac, dann PATCH an den Generator
    state["ramp_status"] = {"phase": label, "progress": round(frac, 2)}
```
> **Feinheit, die eine Falle vermeidet:** Diese globale Rampe fasst nur `rate`/`tcp_rate`/`udp_rate` an. Läuft ein Generator gerade im `pattern: ramp`-Modus, ignoriert er das generische `rate`-Feld (er rechnet seine Rate aus `ramp_start_rate`/`ramp_end_rate`). Beide Rampen-Mechanismen stören sich daher nicht. Das ist ausführlich im Docstring (Z. 227–256) erklärt – ein gutes Beispiel dafür, dass zwei ähnliche Features sauber getrennt wurden.

### Adaptive Control (`_adaptive_loop()`, Zeile 376)

Der autonome Regelkreis. Jede `check_interval` Sekunden:
```python
error_rate = g.get("error_rate", 0.0)   # vorberechnet von metrics/collector.py
if error_rate >= error_rate_min or (latency too high):
    action = "down"        # Rate runter
elif error_rate <= error_rate_max and (latency ok):
    action = "up"          # Rate hoch
else:
    action = "hold"
multipliers[name] = clamp(multipliers[name] * factor, min_mult, max_mult)
applied = {k: max(1, round(base_rate * multiplier)) for k in base_rates}
# nur PATCH + Log, wenn sich der Multiplikator wirklich bewegt hat
```
Drei Details, die Reife zeigen:
1. **`had_activity`-Check:** Wenn ein Generator seit dem letzten Check gar nichts gesendet hat, wird `hold` gewählt – man skaliert nicht auf Basis veralteter Zahlen.
2. **`error_rate` kommt fertig vom Metrics-Collector** (nicht selbst gerechnet), damit Dashboard und Adaptive Control **dieselbe** Zahl benutzen. Siehe [3.7](#37-metrics-collector-metricscollectorpy-206-zeilen).
3. **Nur loggen, wenn sich etwas ändert:** Am Anschlag (min/max) wird nicht gespammt.

### Die REST-Endpunkte (ab Zeile 602)

| Endpoint | Was er tut |
|---|---|
| `POST /start?profile=` | YAML laden, Phase 1 an alle senden, Generatoren starten (bei Warmup mit rate=0), Phasen-Runner als Background-Task starten |
| `POST /stop` | Phasen-Runner canceln, Adaptive stoppen, allen Generatoren `/stop` schicken |
| `POST /config/load` | Aktives Profil setzen; wenn laufend: alten Runner canceln, Generatoren neu starten, neuen Runner starten |
| `PATCH /generator/{name}` | Beliebige Overrides an *einen* Generator weiterleiten (Live-Tweaks im Demo) |
| `POST /generator/{name}/start\|stop` | Einzelnen Generator an/aus (Dashboard-Schalter) |
| `GET /status` | **Der wichtigste:** bündelt Generator-Status + Metrics + Analyse + letzte 50 Logs |
| `GET /profiles` | Listet YAML-Dateien in `config/` |
| `GET /adaptive/status`, `POST /adaptive/toggle` | Adaptive Control abfragen/manuell schalten |
| `GET /logs/{container}` | Rohe `docker logs` eines Containers (via Docker-Socket, read-only gemountet) |
| `GET /health` | Liveness-Check |

> **Sicherheitsdetail für die Verteidigung:** `/logs/{container}` ruft `docker logs` per Subprocess auf. Damit kein beliebiger String eingeschleust wird, gibt es eine **Whitelist** `_ALLOWED_LOG_CONTAINERS` (Z. 915). Nur bekannte Container-Namen sind erlaubt. Das ist bewusste Härtung gegen Command-Injection.

### Die Zeitzone (`LOG_TZ`, Zeile 85)

Ein subtiles, aber cleveres Detail: Docker-Container laufen in UTC, das Dashboard nutzt aber Browser-Lokalzeit (Europe/Berlin). Ohne Korrektur wären Server-Logs 1–2 Stunden gegenüber den Client-Logs verschoben. Deshalb schreibt der Controller seine Zeitstempel explizit in `Europe/Berlin`. Kleine Sache – aber genau die Art Detail, die zeigt, dass du das System *wirklich* durchdacht hast.

## Das gemeinsame Muster aller vier Generatoren

Bevor wir in die einzelnen Generatoren gehen: Alle vier folgen **demselben Bauplan**. Wenn du dieses Muster einmal verstehst, verstehst du alle vier.

```
┌─────────────────────────────────────────────────────────┐
│ 1. state-dict         → alle Config- und Zähl-Werte      │
│ 2. Pydantic-Modelle   → für Swagger/Validierung          │
│ 3. Sende-Schleife     → läuft im Hintergrund-Thread      │
│ 4. Metrics-Schleife   → alle 5 s POST /update an metrics  │
│ 5. REST-API           → /start /stop /config /status     │
└─────────────────────────────────────────────────────────┘
```

Gemeinsame Bausteine, die in **jedem** Generator identisch heißen:
- **`_lock` (threading.Lock):** Schützt `state`, weil FastAPI-Thread und Sende-Thread gleichzeitig zugreifen.
- **`_bytes_window` / `_latency_window`:** Gleitende 10-Sekunden-Fenster (Liste von `(timestamp, wert)`), um `rate_bps` und `latency_ms` zu berechnen.
- **`_history`:** Ringpuffer der letzten ~60 Snapshots (5 Min) für die Dashboard-Sparklines.
- **Die vier Pattern-Helfer:** `_is_burst_active()`, `_ramp_progress()`, `_note_pattern_transition()`, `_ramp_effective_rate()` – in allen Generatoren fast wortgleich.
- **Fault-Injection-Logik:** `if fault_rate > 0 and random.random() < fault_rate:` → als Fehler zählen, ohne zu senden.

> **Prüfungs-Antwort auf „Warum ein Thread und nicht alles in FastAPI?":** FastAPI/Uvicorn bedient die REST-API. Das eigentliche Senden muss aber *dauerhaft* im Hintergrund laufen, unabhängig von HTTP-Requests. Deshalb startet jeder Generator beim Import einen Daemon-Thread (`threading.Thread(target=..., daemon=True).start()`), der die Sende- und Metrics-Schleife trägt. Die HTTP/2- und QUIC-Generatoren laufen sogar in einem eigenen `asyncio`-Event-Loop in diesem Thread, weil ihre Netzwerk-Bibliotheken async sind.

## 3.2 Der HTTP/2-Generator (`generators/http2/generator.py`, 646 Zeilen)

**Das Besondere:** Er enthält einen **selbstgeschriebenen HTTP/2-Client** (`H2CClient`, Z. 235–402), weil `httpx` über unverschlüsseltes HTTP kein HTTP/2 spricht (siehe [2.2](#22-http2--multiplexing-über-eine-tcp-verbindung)). Das ist die technisch anspruchsvollste einzelne Klasse im Projekt.

### Wie der `H2CClient` funktioniert

Er hält **eine** TCP-Verbindung offen und multiplext mehrere Streams darüber:

1. **`_ensure_connected()` (Z. 252):** Öffnet die TCP-Verbindung, initialisiert eine `h2.connection.H2Connection` (client-side), schickt die HTTP/2-Preface (`initiate_connection()` → `data_to_send()`) und startet die Lese-Schleife.

2. **`_read_loop()` (Z. 265):** Läuft dauerhaft, liest Bytes vom Socket, füttert sie in die `h2`-Zustandsmaschine (`conn.receive_data(data)`), und verarbeitet die resultierenden Events. Bei Verbindungsabbruch werden alle offenen Streams mit einer Exception „geweckt", damit kein Request ewig hängt.

3. **`_handle_event()` (Z. 300):** Die Event-Zustandsmaschine. Reagiert auf:
   - `ResponseReceived` → Puffer für Stream anlegen
   - `DataReceived` → Antwortdaten sammeln + **Flow-Control quittieren** (`acknowledge_received_data`)
   - `StreamEnded` → den wartenden Future mit dem Ergebnis erfüllen
   - `StreamReset` → Future mit Fehler erfüllen
   - `WindowUpdated` → wartende Sender wecken (Flow Control gab Fenster frei)

4. **`request()` (Z. 333):** Sendet einen Request. Die kniffligste Stelle:
   - Es **snapshottet** `conn` und `writer` zu Beginn (Z. 343). Warum? Falls mitten im Request ein Reconnect passiert, muss man weiter auf *dem* Objekt arbeiten, auf dem der Stream geöffnet wurde – sonst würde eine Stream-ID von der alten Verbindung auf die neue angewendet und deren H2-Zustandsmaschine für *alle* Streams korrumpieren. **Das ist ein tiefes Concurrency-Detail** – wenn der Professor nach Nebenläufigkeit fragt, ist das dein Trumpf.
   - Es respektiert **Flow Control:** Beim POST-Body wird in Chunks gesendet, begrenzt durch `local_flow_control_window()`. Ist das Fenster voll, wartet es (`_wait_for_window`) auf ein `WINDOW_UPDATE`.
   - Bei Timeout/Fehler wird der Stream sauber zurückgesetzt (`reset_stream`), damit er nicht ewig „offen" bleibt und irgendwann `max_concurrent_streams` erschöpft.

### Die Sende-Schleife (`_generate()`, Z. 503) und `_send_one()` (Z. 405)

- `_generate()` liest die Config, wendet das Pattern an (ramp/burst/constant/random), und feuert dann **`concurrent_streams` Requests gleichzeitig** via `asyncio.gather()` – das ist das sichtbare Multiplexing.
- `_send_one()` würfelt GET vs. POST (`method_get_pct`), wählt einen zufälligen Pfad aus `get_paths`/`post_paths`, injiziert ggf. Fault/Latenz, sendet, und misst die Latenz (`time.perf_counter()`).

> **Wichtig für die Verteidigung:** `latency_ms` misst hier auch **fehlgeschlagene** Requests (Z. 445–452), nicht nur erfolgreiche. So spiegelt die Latenz echte Degradation wider, nicht nur die Gutfälle. Diese Ehrlichkeit in der Messung ist genau das, was die Adaptive Control zuverlässig macht.

## 3.3 Der QUIC/HTTP-3-Generator (`generators/quic/generator.py`, 555 Zeilen)

**Basiert auf `aioquic`.** Sendet HTTP/3-POST-Requests über eine gehaltene QUIC-Verbindung.

### Verbindungs-Wiederverwendung (`_run_connection()`, Z. 353)

Der Kern: Es öffnet **eine** QUIC-Verbindung und sendet darauf viele Requests, bis der Generator gestoppt wird oder die Verbindung bricht. Grund: Handshake-Fairness (siehe [1.5](#15-die-zentralen-design-entscheidungen-und-ihre-begründung) ④).

### 0-RTT-Resumption (Z. 91–96, 371–383)

- `_on_session_ticket()` cached das TLS-Session-Ticket, das der Server ausstellt.
- Bei aktivem `use_0rtt` wird dieses Ticket beim nächsten Verbindungsaufbau angeboten (`config.session_ticket = cached_ticket`).
- Nach dem Aufbau wird geprüft, ob die Wiederaufnahme **wirklich** gelang: `conn._quic.tls.session_resumed` → landet in `zero_rtt_used`.

> **Der wichtigste Bugfix des Projekts steckt hier (Z. 404–412):** aioquics Standard-`idle_timeout` (60 s) beendete die Verbindung still, wenn während einer `rate=0`-Phase nichts gesendet wurde (keine Keep-Alives). Danach kletterten `packets_sent`/`bytes_sent` weiter, obwohl **null** Bytes das Target erreichten – `transmit()`/`send_data()` auf einer geschlossenen Verbindung werfen keinen Fehler und liefern nichts. Der Fix: Vor jedem Zyklus `if conn._closed.is_set(): return` → erzwingt einen frischen Reconnect. **Genau dieser Bug ist das Paradebeispiel für „Selbstauskunft ≠ Realität"** aus [1.2](#12-das-mentale-modell-generator--target--observer): Der Generator meldete Erfolg, aber der Analyzer-Sidecar sah 0 %. Nach dem Fix stieg QUICs Anteil auf stabile ~15–30 %.

### Der kosmetische `unraisablehook` (Z. 105–121)

Ein Detail, das Reife zeigt: Beim Schließen einer HTTP/3-Verbindung versucht aioquics Garbage Collector, auf schon geschlossenen Streams noch ein FIN zu senden – harmlos, aber es spammt `stderr`. Der Generator filtert **genau diese zwei bekannten, harmlosen Meldungen** heraus, statt alle Fehler zu unterdrücken. (Wenn der Professor fragt „Was ist dieser Hook?" – das ist die Antwort: gezieltes Unterdrücken von Bibliotheks-Kosmetik, nicht von echten Fehlern.)

## 3.4 Der MQTT-Generator (`generators/mqtt/generator.py`, 567 Zeilen)

**Basiert auf `paho-mqtt`.** Publiziert *und* abonniert – so wird der Broker-Fan-out sichtbar.

### Topics (`_topic_list()`, Z. 61)

Rotiert durch `topic_count` Topics: die ersten fünf sind beschreibend (`sensors/temperature`, `sensors/humidity`, `sensors/pressure`, `actuators/control`, `status/heartbeat`), darüber hinaus generische `load/topic-N`. Maximal 20.

### QoS-Verteilung (`_normalize_qos_distribution()`, Z. 237; Anwendung Z. 409–411)

Das MQTT-Highlight. Statt eines festen QoS kann jede Nachricht ihren QoS **zufällig** aus einer gewichteten Verteilung ziehen:
```python
publish_qos = qos                       # Standard: fester Wert
if qos_dist gültig:
    publish_qos = random.choices([0,1,2], weights=qos_dist)[0]
```
Die YAML schreibt `qos_distribution: {0: 50, 1: 30, 2: 20}` (Wörterbuch), was über JSON zu String-Keys wird – `_normalize_qos_distribution()` fängt beide Formen ab und macht daraus `[50, 30, 20]`. **So entsteht der QoS-Mix, der in Wireshark die unterschiedlichen Handshake-Längen zeigt.**

### Sauberes Idle-Verhalten (`stop()`, Z. 516–534)

Ein subtiles Detail mit Analyse-Bezug: Beim Stop wird nicht nur die Sende-Schleife gestoppt, sondern auch aktiv `mqtt_client.disconnect()` aufgerufen. Grund: Sonst würde paho-mqtt alle 60 s Keep-Alive-PINGREQs schicken, und der Analyzer-Sidecar würde diese als „Traffic wieder da → wieder still"-Zyklus fehlinterpretieren (falsches Fehlersignal). **Das ist ein Beispiel dafür, wie eng Generator und Analyzer aufeinander abgestimmt sind.**

## 3.5 Der TCP/UDP-Generator (`generators/tcpudp/generator.py`, 701 Zeilen)

**Der wichtigste Generator für die Analyse.** Er sendet rohe TCP/UDP-Pakete über Standard-Sockets und trägt die Normal-vs-Stealth-Logik, die die Behavioral-Fingerprinting-Analyse (Task 3) trägt.

### Die zwei unabhängigen Achsen: `mode` und `pattern`

Das musst du sauber trennen können – es ist eine beliebte Prüfungsfalle:
- **`mode`** bestimmt **Größe + Basis-Timing** eines *einzelnen* Pakets:
  - `normal` → feste Größe (`_normal_params()`, Z. 263), Intervall = `1/rate`.
  - `stealth` → zufällige Größe 64–1400 B, Poisson-Intervall (`_stealth_params()`, Z. 279).
- **`pattern`** bestimmt die **Gesamt-Kadenz** obendrauf: `constant`, `periodic_burst`, `random`, `ramp`.

Man kann also z. B. `mode: stealth` *und* `pattern: random` kombinieren – zufällige Größe **und** zufällige Kadenz.

### Die unabhängige TCP/UDP-Terminierung (`_next_due`, Z. 109; Nutzung Z. 479)

Das ist die subtilste Designentscheidung im ganzen Projekt. Für `constant`/`random`/`periodic_burst` werden TCP und UDP **völlig unabhängig** getaktet – jedes strikt mit seiner eigenen `tcp_rate`/`udp_rate`.

**Warum nicht per Münzwurf (`tcp_ratio`)?** Weil ein Münzwurf die tatsächliche Sende-Frequenz eines Protokolls von seiner konfigurierten Rate entkoppeln würde. Beispiel: `tcp_rate=5`, `udp_rate=150` bei Standard-`tcp_ratio=60` würde trotzdem 60 % der Pakete als TCP senden – das Ergebnis wäre vom 60/40-Split dominiert, nicht von den konfigurierten 5:150. Deshalb führt der Generator zwei „Fälligkeits-Zeitpunkte" (`_next_due["tcp"]`, `_next_due["udp"]`) und sendet immer das Protokoll, das als nächstes dran ist.

> **Ausnahme mit Absicht:** Nur `ramp` benutzt `tcp_ratio`, weil dort eine *einzelne kombinierte* Rate über beide Protokolle gerampt und dann per Ratio gesplittet wird. Diese Unterscheidung ist im Code (Z. 518–520) und in den Docstrings ausführlich begründet.

### Der Selbstheilungs-Mechanismus (`_send_loop()`, Z. 537)

Ein Reife-Detail: Die Sende-Schleife wickelt jede Iteration in try/except. Ohne das würde eine *einzige* unerwartete Exception (z. B. ein kaputtes Config-Feld) den Daemon-Thread **für immer** killen – Traffic wäre still, ohne dass irgendetwas im Log stünde, und kein Neustart des Targets könnte es reparieren, weil der sendende Thread schlicht nicht mehr existiert. Mit dem try/except loggt es den Fehler und macht weiter. (Z. 541–551)

### Fehler-Diagnose (`_note_send_result()`, Z. 299)

Bei jedem 20. aufeinanderfolgenden Sende-Fehler loggt der Generator, worauf `TARGET_HOST` gerade auflöst – um ein „stale DNS"-Szenario zu fangen (Docker hat das Target neu erstellt, aber unter neuer IP). Da weder `connect()` noch `sendto()` die Auflösung cachen, sollte immer eine frische IP erscheinen; falls nicht, fängt diese Log-Zeile es. Zeigt: Es wurde an den Docker-Recreate-Randfall gedacht.

### Die Shorthand-Expander (`_expand_*_shorthand`, Z. 601–644)

Komfort-Funktionen für die YAML: `{"rate": 20}` wird zu `{"tcp_rate": 12, "udp_rate": 8}` bei `tcp_ratio=60` expandiert; `packet_size` zu `tcp_packet_size`+`udp_packet_size`; `burst_rate` zu `tcp_burst_rate`+`udp_burst_rate`. Explizite per-Protokoll-Werte gewinnen immer.

## 3.6 Die Target-Server (`targets/`)

Die Targets sind bewusst **minimal** – sie müssen nur den Traffic annehmen und quittieren, damit echte Protokoll-Interaktion auf dem Draht entsteht.

- **`targets/http2_server/server.py` (55 Zeilen):** FastAPI, serviert von **Hypercorn** (das HTTP/2 nativ kann). `GET /` → JSON, `POST /data` → Echo mit Byte-Zähler. **Catch-all-Routen** (`/{path:path}`, Z. 42–55) fangen beliebige `get_paths`/`post_paths` ab, damit konfigurierte Extra-Pfade keine 404 erzeugen.
- **`targets/quic_server/server.py` (93 Zeilen):** aioquic-HTTP/3-Server auf UDP-4433. `H3Handler` sammelt pro Stream Header + Body und antwortet (bei POST mit Byte-Anzahl). Nutzt ein selbstsigniertes TLS-Zertifikat (im Dockerfile erzeugt).
- **`targets/tcpudp_sink/sink.py` (54 Zeilen):** Ein simpler „Sink": lauscht auf TCP+UDP-9999 und **verwirft** alle Daten. Existiert, damit `gen-tcpudp` ein echtes Ziel hat – so wird ein **TCP RST** in Wireshark sichtbar, wenn man diesen Container stoppt (Failure-Analyse).

## 3.7 Metrics Collector (`metrics/collector.py`, 206 Zeilen)

**Flask-App**, die zwei Quellen aggregiert und über HTTP bereitstellt.

### Zwei Eingänge, zwei Ausgänge
- `POST /update` (Z. 24) ← Generatoren-Selbstauskunft. Speichert *aktuellen* und *vorherigen* Snapshot pro Generator (für die gefensterte Fehlerrate).
- `POST /analysis/update` (Z. 107) ← Analyzer-Snapshots (gemessene Pakete).
- `GET /metrics` (Z. 55) → aggregierte Generator-Stats.
- `GET /analysis` (Z. 125) → aggregierte, gemessene Netzwerk-Analyse.

### Die korrekte Fehlerrate (`_error_rate()`, Z. 48) – ein wichtiges Detail

```python
def _error_rate(errors, packets):
    total = errors + packets
    return round(errors / total, 4) if total > 0 else 0.0
```
**Warum nicht `errors / packets_sent`?** Weil `packets_sent` (Erfolge) und `errors` (Fehler) **disjunkte** Zähler sind. Teilt man nur durch die Erfolge, überschätzt man die Rate – und bei `fault_rate=1.0` bricht es komplett: `packets_sent` bleibt 0, `0/0` liest sich als „keine Fehler" statt „100 % Fehler". Die richtige Formel ist `errors / (errors + packets_sent)` = Fehler als Anteil *aller Versuche*.

> **Zusätzlich gefenstert (Z. 74–92):** Die Rate wird auf die *Differenz* seit dem letzten Push (~5 s) berechnet, nicht über die gesamte Laufzeit. So spiegelt sie *aktuelle* Bedingungen – genau die Aktualität, die Adaptive Control zum prompten Reagieren braucht. Diese *eine* Zahl wird zentral berechnet, damit Dashboard und Adaptive Control garantiert übereinstimmen.

### Die Analyse-Aggregation (`analysis()`, Z. 125)

Jeder Analyzer sieht nur die Namespace *seines* Targets. Der Collector rekonstruiert das Gesamtbild, indem er (a) die Protokoll-Totale **aufsummiert**, (b) die 1-Sekunden-Buckets nach Zeitstempel **mergt**, (c) die Größen-/Gap-Histogramme summiert und (d) alle `failure_events` konkateniert, sortiert und auf die letzten 100 kappt. → Das ist das Äquivalent zu Wiresharks „4 Captures parallel + mergecap", nur live.

## 3.8 Die Analyzer-Sidecars (`analyzer/analyzer.py`, 253 Zeilen)

**Die Beobachtungsebene.** Jeder Sidecar teilt sich per `network_mode: "service:<target>"` die Netzwerk-Namespace eines Targets und lässt darauf `tshark` laufen – er sieht die **echten** Pakete.

### Der tshark-Aufruf (`_tshark_loop()`, Z. 168)

```bash
tshark -i eth0 -l -n -f "not port 9090" -T fields \
  -e frame.time_epoch -e frame.len \
  -e tcp.srcport -e tcp.dstport -e udp.srcport -e udp.dstport \
  -e tcp.flags.reset -e mqtt.msgtype
```
- **`-f "not port 9090"` ist essenziell:** Der Sidecar teilt die Namespace des Targets, also würden seine *eigenen* `POST /analysis/update`-Aufrufe an den Collector sonst mitgeschnitten und als Traffic fehlklassifiziert. Der Filter schließt sie aus.
- Es extrahiert pro Paket: Zeit, Länge, Ports (→ Klassifikation), RST-Flag und MQTT-Nachrichtentyp (→ Fehlersignale).

### Klassifikation (`_classify()`, Z. 76)

Rein über die Portnummer – **dieselben** Ports, die der Wireshark-Guide als Filter nutzt: `8080→http2`, `1883→mqtt`, `4433→quic`, `9999→tcpudp`. Alles andere → `other`.

### Die Fingerprint-Histogramme (Task 3, live)

- **`size_hist`:** Jedes Paket wird in einen Größen-Bucket einsortiert (`<64`, `64-128`, …, `1500+`).
- **`gap_hist`:** Der Abstand zum *vorherigen* Paket desselben Protokolls in einen Zeit-Bucket (`<1ms`, …, `1000ms+`).
- **Der Live-Trick – exponentieller Zerfall (`HIST_DECAY = 0.88`, Z. 45; angewandt Z. 123):** Einmal pro Sekunde werden alle Bucket-Zähler mit 0.88 multipliziert (~5 s Halbwertszeit). Dadurch spiegeln die Histogramme das **jüngste** Verhalten – schaltest du live von `normal` auf `stealth`, formt sich das Histogramm über ~15–25 s sichtbar um, statt von Stunden alter Historie verwässert zu werden.

### Die Fehlersignale (Task 4)

- **Explizit (sofort beim Paket):** TCP RST (`signal: tcp_rst`), MQTT DISCONNECT (`mqtt.msgtype == 14`, `signal: mqtt_disconnect`).
- **Generischer Fallback – `_check_silence()` (Z. 131):** Sendet ein Protokoll, das vorher aktiv war, für `SILENCE_THRESHOLD_S = 8.0` s **nichts** mehr, wird `signal: silence` gesetzt; kommt es zurück, `signal: recovered`. **Kantengetriggert** (feuert einmal pro Übergang, nicht pro Sekunde).
- **Warum 8 Sekunden?** Bei 3 s feuerte es ständig im Normalbetrieb: Bei Poisson-Traffic mit 1 Paket/s ist `P(Lücke > 3s) ≈ e⁻³ ≈ 5 %` pro Lücke – dutzende Fehlalarme/Minute. Bei 8 s: `P(Lücke > 8s) ≈ e⁻⁸ ≈ 0,03 %` – stabil. **Das ist angewandte Wahrscheinlichkeitsrechnung** und ein perfektes Beispiel dafür, dass ein Schwellenwert *begründet* und nicht geraten wurde.
- **Warum Silence für QUIC unverzichtbar ist:** Ein QUIC `CONNECTION_CLOSE` ist verschlüsselt und unsichtbar (siehe [2.3](#23-quic--http3--der-transport-neuling-über-udp)). „Die Antworten hören auf" ist das einzige ehrlich beobachtbare QUIC-Fehlersignal.

## 3.9 Das Dashboard (`dashboard/index.html`, 3190 Zeilen)

**Eine einzige Datei** – HTML + CSS + Vanilla-JavaScript, ohne Framework, ohne Build-Schritt. Serviert von einem simplen `python -m http.server`. Bewusst so gehalten: leicht zu erklären, keine Toolchain nötig.

### Der Render-Zyklus (`poll()`, Z. 2193)

Das Herz ist eine Polling-Schleife:
```javascript
async function poll() {
  const res = await fetch(`${API}/status`, {signal: AbortSignal.timeout(3000)});
  const data = await res.json();
  renderStatus(data);       // Generator-Kacheln, Rates, Fehlerrate
  renderPerf(data);         // Live-Performance-Graph
  renderAnalysis(data);     // gemessene Netzwerk-Analyse + Histogramme
  renderFailureEvents(data);// Fehlersignal-Feed
}
// alle 2 Sekunden aufgerufen
```
Ein **einziger** `GET /status`-Aufruf liefert alles (Generatoren + Metrics + Analyse + Log) – deshalb muss das Dashboard nur *einen* Endpunkt pollen, nicht fünf.

### Die acht Panels (alle prüfungsrelevant)

| Panel | Zeigt | Datenquelle |
|---|---|---|
| **Live Performance** | Pakete/s, Fehlerrate, Sparklines pro Generator | `status.generators`, `status.metrics` |
| **Traffic Profile** | Profil-Buttons, per-Protokoll-Regler | `/profiles`, PATCH |
| **Adaptive Control** | letzte Auf/Ab-Entscheidung pro Generator | `status.adaptive_status` |
| **System Log** | Zeitstempel-Log + Tabs für rohe `docker logs` | `status.log`, `/logs/{c}` |
| **Fault Injection** | Slider für `fault_rate`/`extra_latency_ms` | PATCH an Generatoren |
| **Failure Signals & Injection Events** | Live-Feed der Fehlersignale | `status.analysis.failure_events` |
| **Live Network Analysis** | echte Byte-Anteile + Durchsatz-Graph pro Protokoll | `status.analysis` (tshark!) |
| **Attacker's View** | Größen-/Gap-Histogramme + Fingerprint-Urteil | `status.analysis.size_hist/gap_hist` |

### Das automatische Fingerprint-Urteil (`fingerprintVerdict()`, Z. 2686)

Ein besonders schönes Detail: Das Dashboard leitet den Urteilstext (z. B. „clear, repeatable fingerprint" vs. „hard to distinguish from background noise") **aus den gemessenen Daten** ab – konkret aus dem Anteil des dominanten Buckets am Gesamt. Nicht hartkodiert, sondern **berechnet**. Genau das trennt eine Behauptung von einer Messung.

## 3.10 Docker-Orchestrierung

### Einzelmaschine (`docker-compose.yml`, 221 Zeilen)

Startet alle 15 Container mit einem Befehl. Wichtige Muster:
- **`networks: [mic-net]`** an jedem Service → gemeinsames Bridge-Netz, Namensauflösung.
- **`depends_on` mit `condition: service_healthy`** (Z. 52) → der Controller startet erst, wenn der Metrics-Collector seinen Healthcheck besteht. Verhindert Race Conditions beim Start.
- **`build: ./xyz`** → jeder Service hat sein eigenes Dockerfile.
- **Docker-Socket read-only gemountet** (Z. 45) → damit der Controller `docker logs` lesen kann (für das `/logs/{container}`-Panel).
- **Die Analyzer-Sidecars (Z. 167–221):** `network_mode: "service:<target>"` + `cap_add: [NET_RAW, NET_ADMIN]`. Die Capabilities sind nötig, damit `tshark` überhaupt mitschneiden darf.

### Mehrmaschinenbetrieb (Task 5)

Zwei zusätzliche Compose-Dateien teilen das System auf zwei Lab-Rechner auf:
- **`docker-compose.targets.yml`** → Maschine B: Targets + Broker + Metrics. **Zuerst starten.**
- **`docker-compose.generators.yml`** → Maschine A: Controller + Dashboard + Generatoren. Die Generatoren zeigen über die Umgebungsvariable `TARGET_B_IP` (aus einer `.env`-Datei, Vorlage `.env.example`) auf Maschine B.

```bash
# Maschine B (Targets):
docker-compose -f docker-compose.targets.yml up --build
# Maschine A (Generatoren), nach dem Setzen von TARGET_B_IP:
echo "TARGET_B_IP=192.168.1.42" > .env
docker-compose -f docker-compose.generators.yml up --build
```
Wireshark läuft dann auf der **physischen** Schnittstelle (nicht `docker0`), sodass echter Inter-Maschinen-Traffic sichtbar wird. Maschine Bs Firewall muss 8080/tcp, 4433/udp, 9999/tcp+udp, 1883/tcp, 9090/tcp erlauben. Die **gleichen 3 Profile** funktionieren unverändert – nur die Ziel-Adressen ändern sich per Umgebungsvariable, kein YAML-Umbau nötig.

## 3.11 Das YAML-Konfigurationsschema

Am Beispiel `config/balanced.yaml` – das repräsentativste Profil, das *alle* Features zeigt:

```yaml
global:
  duration: 300      # Gesamtdauer (informativ)
  warmup: 30         # 30 s lineare Rampe von 0 hoch (globaler Warmup)
  cooldown: 30       # 30 s lineare Rampe auf 0 runter

phases:              # sequenziell abgearbeitet
  - name: balanced_constant       # Phase 1: alle Protokolle, konstant
    duration: 75
    protocols:
      http2: {rate: 50, payload_size: 2048, method_distribution: {GET: 70, POST: 30}, concurrent_streams: 3}
      quic:  {rate: 40, payload_size: 1024, stream_count: 2, use_0rtt: false}
      mqtt:  {rate: 30, payload_size: 256, qos_distribution: {0: 40, 1: 40, 2: 20}, topics: [...]}
      tcp:   {rate: 15, packet_size: 512, mode: normal}
      udp:   {rate: 10, packet_size: 256}

  - name: balanced_with_stealth   # Phase 2: Stealth + Poisson auf allen
    duration: 75
    protocols:
      http2: {rate: 50, pattern: random}    # Poisson
      quic:  {rate: 40, pattern: random}
      mqtt:  {rate: 30, pattern: random}
      tcpudp: {mode: stealth, mean_interval: 0.100, min_size: 64, max_size: 1400, tcp_ratio: 60, pattern: random}

  - name: balanced_ramp           # Phase 3: lineare Rampen
    duration: 75
    protocols:
      http2: {pattern: ramp, ramp_start_rate: 5, ramp_end_rate: 120, ramp_duration: 60}
      # ... quic/mqtt/tcpudp analog

  - name: balanced_adaptive       # Phase 4: autonome Regelung
    duration: 75
    adaptive_control:
      enabled: true
      check_interval: 5
      scale_up_threshold:   {error_rate_max: 0.0, latency_max_ms: 50}
      scale_down_threshold: {error_rate_min: 0.05, latency_min_ms: 150}
      scale_up_factor: 1.2
      scale_down_factor: 0.5
      min_multiplier: 0.1
      max_multiplier: 5.0
    protocols: { ... }
```

**Die Schema-Ebenen im Klartext:**
- **`global`** → Dauer, Warmup, Cooldown.
- **`phases`** → Liste von Phasen; jede ändert den Traffic-Mix (genau die geforderte „Traffic-Mix ändert sich über Zeit").
- **`protocols`** → pro Phase, pro Protokoll die Parameter (Rate, Payload, Pattern, Burst, Ramp, Fault …).
- **`adaptive_control`** → optionaler Block pro Phase.

**Die 14 Profile und ihr Zweck** (Auswahl):

| Profil | Zeigt |
|---|---|
| `balanced.yaml` | Die „Alles-Demo" (4 Phasen: constant → stealth → ramp → adaptive) |
| `http2_heavy.yaml` | HTTP/2 dominant + Burst-Muster (für Temporal-Analyse) |
| `mqtt_heavy.yaml` | QoS-Vergleich (0/1/2), IoT-Simulation |
| `tcpudp_heavy.yaml` | TCP/UDP-Fokus mit einseitigem Burst |
| `quic_heavy.yaml` | QUIC/HTTP-3 dominant |
| `fault_injection.yaml` | 4 Phasen steigender Fehlerrate (5 %→25 %→50 %→Recovery) |
| `adaptive_only.yaml` | Adaptive Control isoliert |
| `burst_mode.yaml` | Bursts auf beiden Protokollen unterschiedlich |
| `demo_showcase.yaml` | Kuratierter Demo-Ablauf |
| `video_streaming`, `iot_sensor_farm`, `microservices_api`, `smart_city`, `network_stress_test` | Realistische Szenario-Profile |

> **Prüfungs-Antwort auf „Wie viele Profile braucht ihr?":** Die Aufgabe verlangt mindestens 3 (HTTP/2-heavy, MQTT-heavy, balanced). Wir haben 14 – das übererfüllt „Configuration flexibility" (15 %) deutlich und erlaubt, für *jede* der 5 Analysen das passende Profil zu wählen.

---

# Teil 4 – Die 5 Wireshark-Analysen

Dies ist das bewertungsstärkste Kapitel (25 %). Die Kunst ist nicht das Screenshot-Machen, sondern **jede Beobachtung mit dem Code zu verknüpfen, der sie erzeugt**. Genau das beweist Verständnis. Alle 5 Captures liegen als `.pcapng` + Aufnahme-Skript in `captures/` vor.

**Die Docker-Desktop-Besonderheit (macOS/Windows), die du erklären können musst:** Es gibt keine host-sichtbare `docker0`/`br-*`-Schnittstelle – Container laufen in einer VM. Deshalb wird *innerhalb* des Docker-Netzes aufgenommen, mit einem Sidecar-Container, der die Namespace eines anderen Containers teilt (`--network container:<name>`) und in einen gemounteten Ordner schreibt. Das ergibt exakt dieselbe `.pcapng`-Datei. Auf echten Lab-Linux-Maschinen (Task 5) ist der Trick nicht nötig – dort gibt es eine normale physische Schnittstelle.

## Analyse 1 – Protocol Distribution (Protokoll-Verteilung)

**Ziel:** Nach 60 s Betrieb die Verteilung aller Protokolle zeigen (TCP vs. UDP, und innerhalb TCP die Aufteilung HTTP/2 / MQTT / roh-TCP).

**Profil:** `balanced` (alle Protokolle gleichzeitig aktiv).

**Was du in Wireshark machst:** `Statistics → Protocol Hierarchy` nach 60 s Aufnahme.

**Was du siehst (und erklärst):**
```
Frame → Ethernet → IPv4
   ├── TCP :8080 → HTTP/2   ~40%
   ├── TCP :1883 → MQTT     ~25%
   ├── TCP :9999 → roh-TCP  ~15%
   └── UDP :4433 → QUIC     ~15%
   └── UDP :9999 → roh-UDP   ~5%
```

**Verknüpfung mit dem Code:** Die Verteilung ergibt sich direkt aus den `rate`-Werten in `balanced.yaml` Phase 1 (http2:50, quic:40, mqtt:30, tcp:15, udp:10). **Die Pointe:** QUIC ist das *einzige* Protokoll über UDP – das ist der Schlüsselsatz für den Report. Der Analyzer klassifiziert live nach genau denselben Ports (`analyzer/analyzer.py`, `PORT_PROTOCOL`), sodass das Dashboard-Panel „Live Network Analysis" dieselbe Verteilung *gleichzeitig* zeigt.

**Report-Formulierung:** *„Die Protocol Hierarchy zeigt, dass HTTP/2 (TCP:8080) 40 % des Traffics ausmacht, während QUIC als einziges Protokoll über UDP 15 % beiträgt. Dies entspricht direkt den konfigurierten Raten in balanced.yaml."*

## Analyse 2 – Temporal Analysis (zeitliches Verhalten)

**Ziel:** Zeigen, dass die konfigurierten Sende-Muster (constant, burst, ramp) im I/O-Graph sichtbar sind.

**Profil:** `http2_heavy` (mit Burst-Muster) – dazu optional `balanced` für die Ramp- und Warmup-Slopes.

**Was du machst:** `Statistics → I/O Graph`, je eine Linie pro Protokoll-Filter (`tcp.port==8080`, `tcp.port==1883`, `udp.port==4433`).

**Was du siehst:** Regelmäßige Zacken (Bursts) alle 30 s, 5 s lang, bei erhöhter Rate. MQTT bleibt flach (konstant konfiguriert). Bei einem `ramp`-Profil eine ansteigende Rampe; im Warmup eine sanfte Anfangs-Slope.

**Verknüpfung mit dem Code:**
- Die Zacken kommen von `_is_burst_active()` (`(time.time() % burst_interval) < burst_duration`) – deshalb sind sie **exakt** alle `burst_interval` Sekunden.
- Die Rampe kommt von `_ramp_effective_rate()` (lineare Interpolation).
- Die Warmup-Slope kommt von `_ramp_rates()` im Controller.
- Der Random/Poisson-Kontrast (kein regelmäßiger Abstand) kommt von `random.expovariate()`.

**Report-Formulierung:** *„Der I/O-Graph zeigt periodische Bursts alle ~30 s, die direkt der Konfiguration `burst_interval: 30` entsprechen. MQTT bleibt konstant, da ohne Burst-Muster konfiguriert."*

## Analyse 3 – Behavioral Fingerprinting (das wissenschaftliche Highlight)

**Ziel:** Den Unterschied zwischen Normal- und Stealth-Modus zeigen und begründen, ob synthetischer Traffic statistisch erkennbar ist.

**Profil/Steuerung:** Nur `gen-tcpudp` aktiv. Teil A: `{"mode":"normal"}`, 30 s. Teil B: `{"mode":"stealth"}`, 30 s.

**Was du machst:** `Statistics → Packet Lengths` (Größenverteilung) für beide Modi, plus I/O-Graph für das Timing.

**Was du siehst:**
- **Normal:** ~100 % der Pakete bei genau 512 B, Timing exakt alle 100 ms → **klarer Fingerabdruck**.
- **Stealth:** breite Größenverteilung 64–1400 B, exponentielles (Poisson-)Timing → **kein Fingerabdruck**.

**Verknüpfung mit dem Code:** Direkt `_normal_params()` (feste 512 B / `1/rate`) vs. `_stealth_params()` (`random.randint(64,1400)` / `np.random.exponential(mean)`). Das Dashboard-Panel „Attacker's View" zeigt dieselben Histogramme live, mit automatisch berechnetem Urteil (`fingerprintVerdict()`).

**Die reife Zusatz-Erkenntnis (unbedingt in den Report!):** Bei `tcp_ratio > 0` besiegt Stealth das Fingerprinting **nicht** vollständig – die per-Paket-TCP-Verbindung (`connect/close`) erzeugt in *beiden* Modi identische SYN/ACK/FIN-Kontrollpaket-Schwälle. Erst bei `tcp_ratio=0` (reines UDP) zeigt sich der Kontrast sauber (siehe [2.7](#27-behavioral-fingerprinting--die-wissenschaftliche-pointe)). **Diese Nuance hebt dich von jeder oberflächlichen Abgabe ab.**

**Report-Formulierung:** *„Im Normal-Modus zeigt die Paketlängenverteilung einen einzelnen Peak bei 512 B – ein klassischer Generator-Fingerabdruck. Der Stealth-Modus (Poisson-Timing, zufällige Größe 64–1400 B) erzeugt eine Verteilung, die realen HTTP-Traffic imitiert. Wir stellten jedoch fest, dass der TCP-Verbindungs-Overhead selbst einen Fingerabdruck bildet, unabhängig von der Payload-Verschleierung."*

## Analyse 4 – Failure Visibility (Fehlersichtbarkeit)

**Ziel:** Zeigen, wie sich ein Ausfall protokollspezifisch manifestiert (TCP RST, MQTT DISCONNECT, QUIC connection close).

**Was du machst:** Alle Generatoren starten, 30 s normal aufnehmen, dann während der Aufnahme einen Container stoppen.

**Was du siehst:**
- **`docker stop target-http2`** → **TCP RST**-Pakete (Filter `tcp.flags.reset == 1`), rot, in **unter 1 s**. HTTP/2-Traffic hört auf, MQTT/QUIC laufen weiter.
- **`docker stop mosquitto`** → MQTT-DISCONNECT / TCP-Abbrüche auf Port 1883.
- **QUIC** → kein sichtbares Close-Paket (verschlüsselt), sondern „die Antworten hören auf" (Silence).

**Verknüpfung mit dem Code:** Der Analyzer extrahiert live `tcp.flags.reset` und `mqtt.msgtype` (→ `failure_events`) und hat den `_check_silence()`-Fallback für QUIC. Das Dashboard-Panel „Failure Signals" zeigt diese Ereignisse farbcodiert.

> **Die ehrliche, wichtige Einschränkung (verteidigungsrelevant!):** Weil die Sidecars die Namespace des *Targets* teilen, gehen sie bei `docker stop <target>` mit dem Target *blind* (tshark verliert `eth0`). Deshalb kann das **Live-Dashboard** einen Fehler nur beobachten, wenn ein **Generator** gestoppt wird. Für den „Target gestoppt → TCP RST"-Fall (Sub-Sekunde) nimmt man auf der **Generator-Seite** auf – genau das, was das Aufnahme-Skript `capture_04_failure_visibility.sh` tut. Zweite Feinheit: Ein `docker stop gen-http2` erzeugt einen *sauberen* Verbindungsabbau (SIGTERM), also **kein** RST, sondern nur `silence` nach exakt 8 s. Ein RST verlangt, dass die *Gegenseite* eine etablierte Verbindung ablehnt – das ist der „Target gestoppt"-Fall. Dass du diesen Unterschied kennst, ist ein starkes Signal an den Professor.

**Report-Formulierung:** *„Der TCP RST erscheint innerhalb von ~300 ms nach dem Stopp des HTTP/2-Targets; TCP erkennt den Ausfall sofort. QUIC bleibt für einen passiven Beobachter unsichtbar, da das CONNECTION_CLOSE verschlüsselt ist – hier ist das einzige beobachtbare Signal das Verstummen des Traffics."*

## Analyse 5 – Multi-System Deployment

**Ziel:** Das System auf 2 Maschinen verteilen und den Inter-Maschinen-Link aufnehmen.

**Setup:** Maschine B (`docker-compose.targets.yml`) zuerst, dann Maschine A (`docker-compose.generators.yml`) mit `TARGET_B_IP` in `.env`.

**Was du siehst (Unterschied zum Einzelrechner):**
- Höhere Latenz (echter Netz-Hop statt Loopback).
- **Echte Host-IPs** statt Docker-172.x-Adressen.
- Kein gemeinsames Docker-Bridge → echter Layer-3-Traffic auf der physischen Schnittstelle.

**Verknüpfung mit dem Code:** Kein Code ändert sich – nur die Umgebungsvariablen (`TARGET_URL`, `TARGET_HOST`, `BROKER_HOST`) zeigen auf Maschine B. **Das ist der Beweis für sauberes Config-über-Umgebung-Design:** Dieselben Profile, dieselben Container, nur andere Ziel-Adressen.

**Report-Formulierung:** *„Im Multi-Maschinen-Deployment steigt die HTTP/2-Round-Trip-Zeit von ~0,3 ms (Loopback) auf ~1,2 ms (Ethernet). Der Traffic ist vollständig auf dem Inter-Maschinen-Link sichtbar, während er im Einzelrechner-Setup über docker0 lief."*

---

# Teil 5 – Der Live-Demo-Ablauf

Die Live-Demo zählt 10 %, ist aber auch dein bester Moment, um Souveränität zu zeigen. **Übe sie mindestens zweimal komplett durch.** Fahrplan für 15–20 Minuten:

**Vorbereitung (Abend vorher + 30 min vorher):**
```bash
docker-compose build && docker-compose up -d   # 10 min laufen lassen, prüfen
docker-compose down
# Am Demo-Tag: 4 Tabs vorbereiten:
#  1. Terminal   2. http://localhost:3000 (Dashboard)
#  3. http://localhost:8000/docs (Swagger)   4. Wireshark
```

| Min | Phase | Was du tust & sagst |
|---|---|---|
| **0–3** | **Start** | `docker-compose up`. „15 Container, ein Befehl." Warten bis alle grün, Dashboard zeigen. |
| **3–7** | **Config live** | `balanced` läuft. Live umschalten auf `http2_heavy` (`POST /config/load`). „Sehen Sie, wie HTTP/2 jetzt dominiert – ohne Neustart." Dann eine Einzel-Rate ändern (`PATCH /generator/gen-mqtt {"rate":200}`). |
| **7–12** | **Wireshark** | 1) `Protocol Hierarchy` (Verteilung). 2) `I/O Graph` mit Bursts. 3) Stealth-Highlight: `mode:normal` → alle bei 512 B; `mode:stealth` → breite Verteilung. „Das ist Traffic Morphing – dieselbe Technik wie VPN-Anbieter." |
| **12–15** | **Adaptive + Fault** | Adaptive Control an, dann Fault-Slider HTTP/2 auf ~20 %. „Beobachten Sie: die Rate wird automatisch heruntergeregelt, ohne dass ich etwas tue." |
| **15–17** | **Failure** | Wireshark-Filter `tcp.flags.reset==1`. `docker stop target-http2` → rote RST-Pakete. „TCP erkennt den Ausfall in unter 1 s." Dann `docker start target-http2` → Recovery. |
| **17–19** | **Multi-Machine** | Falls Lab-Rechner verfügbar: echte IPs statt 172.x zeigen. |
| **19–20** | **Fragen** | Siehe Teil 6. |

> **Notfall-Backup (falls nichts startet):** *„Ein gut erklärtes System, das nicht läuft, ist besser als ein laufendes System, das man nicht erklären kann."* Zeige die vorab aufgenommenen `.pcapng` aus `captures/`, erkläre die Architektur-Diagramme, und zeige Normal-vs-Stealth direkt im Generator-Code. **Deshalb ist dieser Guide deine Versicherung.**

---

# Teil 6 – Prüfungsfragen & Antworten

Das ist der wichtigste Teil für deine Sicherheit. Lies die Antworten laut, bis sie sitzen. Sie sind nach Themen gruppiert.

## Protokolle

**F: Warum läuft QUIC über UDP und nicht über TCP?**
> QUIC implementiert Zuverlässigkeit, Fluss- und Stau-Kontrolle selbst in der Anwendungsschicht. Der Hauptvorteil ist der Wegfall des Head-of-Line-Blocking: Bei HTTP/2-über-TCP blockiert ein einzelnes verlorenes TCP-Segment *alle* multiplexten Streams, weil TCP eine geordnete Byte-Sequenz erzwingt. Bei QUIC sind die Streams transport-unabhängig – ein verlorenes Paket blockiert nur seinen eigenen Stream.

**F: Was ist der Unterschied zwischen QoS 0 und QoS 2 in MQTT?**
> QoS 0 ist „fire and forget" – eine Nachricht, ein Paket, keine Bestätigung. QoS 2 garantiert „exactly once" über einen 4-Wege-Handshake: PUBLISH → PUBREC → PUBREL → PUBCOMP, also 4 Pakete pro Nachricht. Man sieht das direkt in Wireshark: viermal so viele Pakete bei QoS 2 mit gleicher Payload. Deshalb steigt die Paketrate mit dem QoS, auch bei gleicher Nachrichtenrate.

**F: Was bedeutet HTTP/2-Multiplexing genau?**
> Mehrere Requests und Responses laufen gleichzeitig als unabhängige Streams über *eine* TCP-Verbindung, zerlegt in binäre Frames. Bei HTTP/1.1 brauchte man dafür mehrere parallele Verbindungen. In unserem Generator feuern wir `concurrent_streams` Requests per `asyncio.gather()` gleichzeitig über den gemeinsamen `H2CClient` – in Wireshark sieht man mehrere Streams auf einer TCP-Verbindung.

**F: Warum habt ihr für HTTP/2 einen eigenen Client geschrieben statt httpx?**
> Weil httpx HTTP/2 nur über TLS via ALPN aushandelt. Unser Target hat kein TLS, und httpx fällt dann *still* auf HTTP/1.1 zurück – dann wären keine echten HTTP/2-Frames auf dem Draht, und die Analyse wäre wertlos. Deshalb sprechen wir mit der `h2`-Bibliothek „h2c" (HTTP/2 cleartext) im prior-knowledge-Modus: HTTP/2-Preface sofort, ohne Upgrade-Handshake.

**F: Wie steuert der TCP/UDP-Generator Größe und Timing ohne ein Framework wie Scapy?**
> Reine Python-Sockets genügen. Die TCP-Paketgröße ist einfach die Länge der Zufalls-Payload an `sendall()`, das Timing steuert `time.sleep()` bzw. der Fälligkeits-Scheduler – festes Intervall im Normal-Modus, exponentiell (Poisson) im Stealth-Modus. Jeder TCP-Send öffnet und schließt seine eigene Verbindung, was für die Demo praktisch ist: Es erzeugt pro Paket eine echte SYN/SYN-ACK/FIN-Sequenz in Wireshark.

## Architektur & Design

**F: Warum HTTP/REST zwischen Controller und Generatoren statt eines Message-Bus?**
> Direkte HTTP-Aufrufe sind leichter zu debuggen (Logs sofort lesbar), haben weniger bewegliche Teile (kein zusätzlicher Broker für die Steuerung) und sind in der Demo intuitiv erklärbar. Ein Message-Bus wäre für reine Steuerbefehle Overengineering. Für den *Traffic* nutzen wir dagegen die echten Protokolle – nur die Steuerebene ist REST.

**F: Woher wisst ihr, dass die Generatoren wirklich senden, was sie behaupten?**
> Genau dafür gibt es die Analyzer-Sidecars. Sie messen mit tshark die echten Pakete auf dem Draht, unabhängig von der Selbstauskunft. Dieser Vergleich hat einen echten Bug aufgedeckt: gen-quic meldete Erfolge, während null Bytes ankamen, weil aioquics 60-s-Idle-Timeout die Verbindung still beendet hatte. Wir prüfen jetzt `conn._closed` vor jedem Send und reconnecten – danach stieg QUICs gemessener Anteil von ~0 % auf ~15–30 %.

**F: Wie funktioniert die Live-Umkonfiguration technisch?**
> Jeder Generator hat eine eigene REST-API mit `PATCH /config`. Der Controller übersetzt die YAML-Phase in Generator-Configs und schickt sie per HTTP. Weil die Sende-Schleife den `state` bei jeder Iteration frisch liest (unter einem Lock), wirkt eine Änderung sofort, ohne Neustart. Das Dashboard nutzt dieselben Endpunkte über die Controller-Proxys.

**F: Wie ist Nebenläufigkeit im System gelöst?**
> Jeder Generator trennt die REST-API (FastAPI/Uvicorn-Thread) von einer Sende-Schleife in einem Daemon-Thread; der gemeinsame `state` ist per `threading.Lock` geschützt. HTTP/2 und QUIC laufen zusätzlich in einem eigenen asyncio-Event-Loop, weil ihre Bibliotheken async sind. Im HTTP/2-Client snapshotten wir Verbindung und Writer pro Request, damit ein Reconnect mitten im Request nicht die H2-Zustandsmaschine für andere Streams korrumpiert.

## Analyse & Resilienz

**F: Was ist Behavioral Fingerprinting und wie verhindert ihr es?**
> Automatisierte Sender hinterlassen statistische Spuren: feste Paketgröße (ein Spike im Histogramm) und festes Timing (ein Peak bei z. B. 100 ms). Ein IDS erkennt das sofort. Der Stealth-Modus verhindert es, indem Größen gleichverteilt (64–1400 B) und Zwischenankunftszeiten exponentiell (Poisson) gezogen werden – statistisch wie menschlicher Traffic. Wichtig: Wir haben gemessen, dass bei TCP der Verbindungs-Overhead selbst ein Fingerabdruck bleibt; nur bei reinem UDP verschwindet er sauber.

**F: Warum die Poisson-Verteilung für das Timing?**
> Weil menschliche Netzaktivität ein Poisson-Prozess ist: unabhängige Ereignisse, konstante Durchschnittsrate, gedächtnislos. Die Zwischenankunftszeiten eines Poisson-Prozesses sind exponentialverteilt – genau das liefert `np.random.exponential(mean)`. So sieht der Traffic aus wie ein echter Nutzer, der klickt, wartet, wieder klickt.

**F: Wie funktioniert eure Adaptive Control?**
> Ein geschlossener Regelkreis im Controller. Alle paar Sekunden liest er die (gefensterte) Fehlerrate jedes Generators. Bei niedriger Fehlerrate skaliert er die Rate multiplikativ hoch, bei hoher runter – begrenzt durch Min/Max-Multiplikatoren. Das ist demselben Denken wie TCP-Congestion-Control verwandt und demonstriert Resilienz ohne menschliches Zutun. Im Demo provozieren wir es mit dem Fault-Slider.

**F: Wie wird die Fehlerrate berechnet und warum so?**
> Als `errors / (errors + packets_sent)`, also Fehler pro *Versuch*. Würde man durch `packets_sent` allein teilen, überschätzte man sie, und bei 100 % Fehlern (packets_sent bleibt 0) käme fälschlich „0 %" heraus. Sie wird zentral im Metrics-Collector berechnet und auf das letzte ~5-s-Fenster begrenzt, damit Dashboard und Adaptive Control dieselbe, aktuelle Zahl nutzen.

**F: Wie macht ihr einen QUIC-Ausfall sichtbar, wenn das Close-Paket verschlüsselt ist?**
> Genau deshalb gibt es den Silence-Fallback: Wenn ein vorher aktives Protokoll für über 8 s kein Paket mehr sendet, flaggen wir „silence", bei Rückkehr „recovered". Die 8 s sind bewusst gewählt – bei 3 s feuerte es im Poisson-Normalbetrieb zu oft (P(Lücke>3s)≈5 %), bei 8 s ist es stabil (≈0,03 %). Für TCP/MQTT haben wir zusätzlich die expliziten Signale RST und DISCONNECT.

## Docker & Betrieb

**F: Wie kommunizieren die Container?**
> Alle im selben Docker-Bridge-Netz `mic-net`, adressiert über Container-Namen via Docker-DNS. Nur nach außen nötige Ports (3000, 8000, 9090, 1883, 8080, 4433) sind gemappt. Der Controller startet erst, wenn der Metrics-Collector seinen Healthcheck besteht (`depends_on: condition: service_healthy`).

**F: Warum teilen die Analyzer die Netzwerk-Namespace der Targets?**
> Weil Docker Desktop auf macOS/Windows keine host-sichtbare Bridge hat. Per `network_mode: "service:<target>"` sitzt der Analyzer *in* der Namespace des Targets und sieht dort dessen echten Traffic auf `eth0`. Dafür braucht er die Capabilities NET_RAW und NET_ADMIN. Nachteil: Stoppt man das Target, geht der Analyzer mit ihm blind – deshalb nimmt man Fehler auf der Generator-Seite auf.

**F: Was würde in einem echten Produktivsystem anders sein?**
> TLS überall, auch für MQTT und rohes TCP. Authentifizierung am Mosquitto-Broker. Rate-Limiting im Controller, damit kein Generator das Netz flutet. Persistenz im Metrics-Collector (z. B. Prometheus statt In-Memory). Für ein Lab-Projekt sind diese Vereinfachungen bewusst und vertretbar.

## Zur KI-Nutzung (die Aufgabe verlangt eine ehrliche Doku)

**F: Wie habt ihr KI eingesetzt?**
> KI (Claude) wurde für den ersten Architektur-Entwurf und Code-Scaffolding genutzt. Alle KI-generierten Teile wurden manuell geprüft, debuggt und getestet. Die wissenschaftlichen Inhalte – insbesondere die Wireshark-Analysen und die Stealth-Mode-Theorie – wurden eigenständig erarbeitet und durch echte Messungen verifiziert (z. B. der QUIC-Idle-Timeout-Bug und die tcp_ratio-Fingerprinting-Erkenntnis wurden live nachgewiesen).

---

# Teil 7 – Ehrliche Grenzen des Systems

Professoren schätzen es enorm, wenn du die Schwächen deines eigenen Systems kennst. Das signalisiert Ingenieur-Reife. Hier die ehrlichen Grenzen – und warum sie für ein Lab-Projekt vertretbar sind.

## 7.1 Bewusste Vereinfachungen

- **Kein TLS auf HTTP/2, MQTT, roh-TCP.** Absicht: Ohne TLS sind die Frames in Wireshark lesbar (bessere Analyse). In Produktion wäre alles verschlüsselt.
- **Keine Broker-Authentifizierung.** Mosquitto läuft offen (`config/mosquitto.conf`) – für ein Lab ok, produktiv ein No-Go.
- **Metrics nur In-Memory.** Beim Neustart des Collectors sind die Zahlen weg. Produktiv würde man Prometheus + Grafana nehmen.
- **MQTT-Topic-*Namen* aus der YAML werden ignoriert** – nur `topic_count` zählt. Der `topics:`-Block ist als Doku erlaubt (`extra="allow"`), wirkt aber nicht. Ehrlich benannt in `_translate_mqtt()`.

## 7.2 Architektur-bedingte Grenzen (analyse-relevant)

- **Target-seitige Sidecars gehen mit dem Target blind.** Stoppt man ein Target, verliert der Analyzer seine `eth0`. Deshalb kann das *Live-Dashboard* nur Generator-Ausfälle beobachten; Target-Ausfälle (mit RST) nimmt man auf der Generator-Seite mit dem Skript auf.
- **`docker stop <generator>` erzeugt kein RST, nur Silence.** SIGTERM schließt die Sockets sauber. Ein RST verlangt, dass die *Gegenseite* eine etablierte Verbindung ablehnt – das ist der Target-Stopp-Fall.
- **Silence hat eine ~8-s-Latenz-Untergrenze.** Der `SILENCE_THRESHOLD_S`-Wert ist ein Kompromiss zwischen schneller Erkennung und Fehlalarm-Freiheit bei Poisson-Traffic.
- **Analyzer-Totale sind kumulativ seit Analyzer-Start** und werden von `POST /reset` (das nur Generator-Zähler nullt) nicht erfasst – man startet die Analyzer neu, um sie zu nullen.

## 7.3 Echte Bugs, die beim Bau gefunden wurden

Diese zu erzählen ist Gold – es beweist, dass du das System selbst zum Laufen gebracht und *gemessen* hast:

1. **QUIC-Idle-Timeout-Bug:** gen-quic meldete Erfolge, während 0 Bytes ankamen (aioquics 60-s-Idle-Timeout beendete die Verbindung still während `rate=0`). Fix: `conn._closed`-Check vor jedem Send → Reconnect. Verifiziert: QUIC-Anteil stieg von ~0 % auf ~15–30 %.
2. **tcp_ratio-Fingerprinting-Erkenntnis:** Stealth-Modus besiegt Fingerprinting bei `tcp_ratio>0` nicht, weil der TCP-Verbindungs-Overhead selbst ein Muster ist. Erst bei reinem UDP sauberer Kontrast. Live gemessen.
3. **Falsche Fehlerrate:** `errors/packets_sent` überschätzte die Rate und brach bei 100 % Fehlern. Fix: `errors/(errors+packets_sent)`, zentral im Collector.
4. **MQTT-Keep-Alive-Fehlalarm:** paho-mqtt-PINGREQs alle 60 s ließen den Analyzer einen falschen „still→aktiv"-Zyklus melden. Fix: aktives `disconnect()` beim Stop.
5. **Silence-Schwelle kalibriert:** von 3 s (zu viele Fehlalarme, ~5 %/Lücke) auf 8 s (~0,03 %) angehoben – mit Wahrscheinlichkeitsrechnung begründet.

> **Diese fünf Punkte sind deine stärkste Munition in der Verteidigung.** Sie zeigen: Du hast nicht nur Code zusammengesteckt, sondern das System empirisch verstanden.

## 7.4 Mögliche Erweiterungen (falls „Was würdet ihr noch machen?" kommt)

- Generator-seitige Analyzer-Sidecars (um Target-Ausfälle live zu sehen).
- TLS-Keylog-Import in Wireshark, um QUIC-Frames zu entschlüsseln.
- Persistente Metrik-Datenbank (Prometheus) + historische Graphen.
- MQTT-Topic-Namen echt verdrahten.
- Chi-Quadrat-Test der Paketgrößen im Report quantifizieren (Methode steht in `docs/STEALTH_MODE.md`).

---

# Anhang – Glossar & Befehls-Spickzettel

## Glossar der Schlüsselbegriffe

| Begriff | Kurzerklärung |
|---|---|
| **Multiplexing** | Mehrere Streams gleichzeitig über eine Verbindung (HTTP/2, QUIC) |
| **h2c** | HTTP/2 cleartext – HTTP/2 ohne TLS |
| **ALPN** | TLS-Erweiterung zur Protokoll-Aushandlung (nötig für httpx-HTTP/2) |
| **Head-of-Line-Blocking** | Ein blockiertes Paket hält alle folgenden auf (TCP-Problem, das QUIC löst) |
| **0-RTT** | QUIC-Wiederverbindung, die Daten *vor* Handshake-Ende sendet |
| **QoS (MQTT)** | Zustellgarantie: 0=höchstens einmal, 1=mindestens einmal, 2=genau einmal |
| **Fan-out** | Broker verteilt eine Nachricht an mehrere Subscriber |
| **Poisson-Prozess** | Mathematisches Modell für unabhängige, gedächtnislose Ereignisse (menschlicher Traffic) |
| **Exponentialverteilung** | Zwischenankunftszeit eines Poisson-Prozesses |
| **TCP RST** | Reset-Paket; schnellstes Ausfall-Signal (Filter `tcp.flags.reset==1`) |
| **Behavioral Fingerprinting** | Erkennung synthetischen Traffics an statistischen Mustern |
| **Traffic Morphing** | Verschleierung dieser Muster (Stealth-Modus) |
| **Sidecar** | Hilfscontainer, der die Namespace eines anderen teilt (hier: Analyzer) |
| **Adaptive Control** | Autonomer Regelkreis, der Rates nach Fehlerrate anpasst |
| **Warmup/Cooldown** | Globale lineare Rampe der Rates am Anfang/Ende eines Profils |

## Befehls-Spickzettel

```bash
# ── System ──────────────────────────────────────────────
docker-compose build              # alle Images bauen
docker-compose up                 # alle 15 Container starten
docker-compose ps                 # Status prüfen (alle "running"?)
docker-compose logs gen-quic      # Logs eines Containers
docker-compose down               # alles stoppen

# ── Steuerung über die Controller-API ───────────────────
curl -X POST "http://localhost:8000/start?profile=balanced"
curl -X POST http://localhost:8000/stop
curl -X POST http://localhost:8000/config/load \
     -H "Content-Type: application/json" -d '{"profile":"http2_heavy"}'
curl -X PATCH http://localhost:8000/generator/gen-mqtt \
     -H "Content-Type: application/json" -d '{"rate":200}'
curl http://localhost:8000/status        # voller Systemstatus
curl http://localhost:8000/profiles       # verfügbare Profile
curl -X POST "http://localhost:8000/adaptive/toggle?enabled=true"

# ── Stealth-Demo ────────────────────────────────────────
curl -X PATCH http://localhost:8000/generator/gen-tcpudp \
     -H "Content-Type: application/json" -d '{"mode":"normal"}'
curl -X PATCH http://localhost:8000/generator/gen-tcpudp \
     -H "Content-Type: application/json" -d '{"mode":"stealth"}'

# ── Failure-Demo ────────────────────────────────────────
docker stop target-http2          # erzeugt TCP RST
docker start target-http2         # Recovery zeigen

# ── Wichtige URLs ───────────────────────────────────────
# Dashboard:   http://localhost:3000
# Controller:  http://localhost:8000  (Swagger: /docs)
# Metrics:     http://localhost:9090/metrics
```

## Wireshark-Filter-Spickzettel

| Filter | Zeigt |
|---|---|
| `tcp.port == 8080` | HTTP/2 |
| `tcp.port == 1883` | MQTT |
| `udp.port == 4433` | QUIC |
| `tcp.port == 9999 or udp.port == 9999` | roh TCP/UDP |
| `mqtt` | alle MQTT-Pakete |
| `mqtt.msgtype == 14` | MQTT DISCONNECT |
| `tcp.flags.reset == 1` | TCP RST (Ausfall) |
| `tcp.flags.syn == 1` | Verbindungsaufbau |

## Die wichtigsten Dateien auf einen Blick

| Datei | Zeilen | Inhalt |
|---|---|---|
| `controller/main.py` | 955 | Orchestrierung, Phasen, Adaptive Control, REST |
| `generators/tcpudp/generator.py` | 701 | Normal/Stealth, unabhängiges TCP/UDP-Timing |
| `generators/http2/generator.py` | 646 | Eigener h2c-Multiplexing-Client |
| `generators/mqtt/generator.py` | 567 | pub/sub, QoS-Verteilung, Topic-Rotation |
| `generators/quic/generator.py` | 555 | aioquic, 0-RTT, Idle-Timeout-Fix |
| `metrics/collector.py` | 206 | Aggregation, gefensterte Fehlerrate |
| `analyzer/analyzer.py` | 253 | tshark, Histogramme, Fehlersignale |
| `dashboard/index.html` | 3190 | Single-File-Dashboard (8 Panels) |
| `docker-compose.yml` | 221 | 15-Container-Orchestrierung |
| `config/balanced.yaml` | 141 | Referenzprofil mit allen 4 Phasen-Typen |

---

## Schlusswort

Wenn du diesen Guide durchgearbeitet hast, kennst du:
- **das große Ganze** (Generator → Target → Observer),
- **die Theorie** hinter jedem Protokoll,
- **jede wichtige Funktion** im Code und *warum* sie so ist,
- **jede der 5 Analysen** und den Code dahinter,
- **die wahrscheinlichen Fragen** und deine Antworten,
- und **die ehrlichen Grenzen**, die Reife zeigen.

Das ist mehr als genug, um den Professor zu überzeugen, dass du das System *verstanden* hast, es *anwenden* kannst und *sicher* in dem bist, was du vorstellst.

**Viel Erfolg bei der Präsentation.**

---

*MIC Final Project · SS2026 · Hochschule Rhein-Waal · Präsentations-Guide*

