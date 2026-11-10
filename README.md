# Invoice Desk

Portal do przygotowania, sprawdzania i księgowania dokumentów klientów. Łączy podgląd oryginału, formularz faktury, przygotowanie danych przez AI, podział zbiorczych skanów, księgę klienta i plany amortyzacji.

Dokumentacja opisuje stan kodu po aktualizacjach: katalog kont GnuCash, księgowanie i korekty, księga klienta, amortyzacja ze zdarzeniami utraty, widok mobilny oraz automatyczny podział skanów. Stan opisu: **10.11.2026**.

## Spis treści

1. [Wybór sposobu uruchomienia](#wybór-sposobu-uruchomienia)
2. [Możliwości i zakres](#możliwości-i-zakres)
3. [Przebieg pracy](#przebieg-pracy)
4. [Budowa aplikacji](#budowa-aplikacji)
5. [Baza danych](#baza-danych)
6. [Wymagania](#wymagania)
7. [Uruchomienie bez Dockera](#uruchomienie-bez-dockera)
8. [Przygotowanie bazy i konta pracownika](#przygotowanie-bazy-i-konta-pracownika)
9. [Konfiguracja](#konfiguracja)
10. [Odczyt dokumentów przez AI](#odczyt-dokumentów-przez-ai)
11. [Rozdzielanie dokumentów](#rozdzielanie-dokumentów)
12. [Księgowanie, korekty i księga klienta](#księgowanie-korekty-i-księga-klienta)
13. [Amortyzacja](#amortyzacja)
14. [Role i zabezpieczenia](#role-i-zabezpieczenia)
15. [Usługa systemowa i HTTPS](#usługa-systemowa-i-https)
16. [Uruchomienie w Dockerze](#uruchomienie-w-dockerze)
17. [Aktualizacje i kopie zapasowe](#aktualizacje-i-kopie-zapasowe)
18. [Diagnostyka i testy](#diagnostyka-i-testy)
19. [Główne endpointy](#główne-endpointy)

## Wybór sposobu uruchomienia

Wybierz **jeden wariant uruchomienia aplikacji**. Funkcje, baza PostgreSQL i sposób kontroli faktur pozostają takie same.

| Wariant | Co uruchamiasz | Kiedy wybrać | Instrukcja |
|---|---|---|---|
| Bez Dockera | Python w `.venv`, WWW przez Uvicorn; opcjonalnie usługa systemd. | Gdy korzystasz z istniejącej instalacji na serwerze lub chcesz zarządzać procesami bez kontenerów. | [Uruchomienie bez Dockera](#uruchomienie-bez-dockera) |
| Docker | Obraz aplikacji i kontenery zarządzane przez Compose. | Gdy masz sprawny Docker i chcesz uruchamiać aplikację w kontenerach. | [Uruchomienie w Dockerze](#uruchomienie-w-dockerze) |

Niezależnie od powyższego wybierz **jeden sposób przygotowania danych przez AI**:

| AI | Bez Dockera | Docker |
|---|---|---|
| MCP | WWW + podłączony klient AI; bez `app.worker`. | `compose.yml` + klient AI; bez `compose.background.yml`. |
| OpenAI API | WWW + worker API. | `compose.yml` + `compose.background.yml`. |

W obu przypadkach ustaw `BACKGROUND_PROCESSING=1`. W trybie MCP poprawne jest `AI_ENABLED=0`, ponieważ AI działa po stronie klienta MCP. Dla API ustaw `AI_ENABLED=1` i skonfiguruj klucz. [Pełny opis AI](#odczyt-dokumentów-przez-ai) wyjaśnia różnice.

Najpierw przygotuj bazę i dostęp do magazynu, potem konfigurację AI, a następnie uruchom wybrany wariant aplikacji. W działającym wdrożeniu zachowaj bieżące dane i konfigurację; nie wykonuj obu instrukcji instalacji równocześnie.

## Możliwości i zakres

- Praca na dokumentach wybranego klienta: zakup, sprzedaż, pozycje, kontrahenci i kwoty.
- Przesyłanie PDF, JPG i PNG; podgląd stron i pobieranie oryginału po zalogowaniu.
- Formularz obok oryginału na dużym ekranie; przełączanie formularza i podglądu na mniejszych ekranach.
- AI przygotowuje dane faktury oraz proponuje konta i kody VAT przez OpenAI API albo klienta MCP. Pracownik sprawdza wynik i w razie potrzeby go poprawia.
- Automatyczny podział czytelnych skanów z kilkoma dokumentami; proponowane ramki i podział ręczny przy niepewnym wyniku.
- Jeden wybór kategorii/konta, wyszukiwanie kont po numerze lub nazwie oraz osobny kod VAT.
- Księgowanie obsługiwanych faktur w jednym kroku, z zapisem w księdze i rejestrze VAT.
- Korekty zaksięgowanych faktur przez administratora lub głównego księgowego.
- Księga klienta za okres: aktualne zapisy, szczegóły, historia zmian, należności i zobowiązania.
- Plany amortyzacji pozycji Inventaris, notatki oraz oznaczenie kradzieży lub zgubienia.
- Kontrola duplikatów, współbieżnych zmian i zamkniętych okresów; audyt operacji.

**Granica obecnego silnika księgowania:** automatyczne dekrety obejmują zwykłe krajowe faktury zakupu i sprzedaży w EUR, z obsługiwanymi kodami VAT. Samo przyjęcie pliku, rozpoznanie paragonu lub dostępność konta w katalogu nie oznaczają, że dany przypadek można zaksięgować tym silnikiem. Faktury zagraniczne, Verlegd/ICP, korekty handlowe i inne waluty wymagają odrębnej obsługi. Szczegóły są w sekcji o księgowaniu.

Projekt nie zawiera pełnego modułu bankowego, deklaracji podatkowych, list płac ani automatycznej synchronizacji księgowań z GnuCash. Część tabel wspólnego schematu bazy przygotowano dla takich modułów, ale ich obecność nie oznacza wdrożonej funkcji.

## Przebieg pracy

1. Pracownik loguje się i wybiera klienta.
2. Dodaje dokumenty. Program zapisuje oryginały i sprawdza, czy skan można podzielić na osobne dokumenty.
3. AI przygotowuje propozycję danych faktury, w tym konta i kody VAT. Dokument oczekuje w kolejce; pracownik może przejść do innego dokumentu.
4. Pracownik porównuje formularz z oryginałem, poprawia pola oraz potwierdza sprawdzenie.
5. Kliknięcie **Zaksięguj fakturę** zapisuje fakturę, pozycje, zbilansowany dekret i rejestr VAT w jednej transakcji.
6. Faktura pojawia się w zapisanych dokumentach i księdze klienta. Zakup na koncie Inventaris udostępnia plan amortyzacji.

Dane i klasyfikację przygotowuje AI. Rolą pracownika jest porównanie formularza z oryginałem, poprawienie błędów lub braków i potwierdzenie sprawdzenia — nie uzupełnianie każdej faktury od początku. AI nie zastępuje tej kontroli i nie księguje samodzielnie dokumentów. Niepewne dopasowania kont lub VAT mogą pozostać puste i wymagają poprawienia przed księgowaniem.

### Trzy różne rodzaje statusów

Status pliku w obiegu, status zadania odczytu i status faktury są oddzielne.

| Warstwa | Najważniejsze wartości | Znaczenie |
|---|---|---|
| Dokument | `to_book` | Dokument oczekuje na obsługę. |
| Dokument | `booked` | Dokument został zaksięgowany. |
| Dokument | `archived` | Np. oryginał zastąpiony wycinkami; pozostaje zachowany. |
| Odczyt | `queued`, `processing` | Oczekiwanie lub trwające przygotowanie propozycji. |
| Odczyt | `ready`, `review` | Propozycja jest gotowa albo wymaga szczególnej uwagi. |
| Odczyt | `failed`, `cancelled` | Odczyt nie powiódł się lub został przerwany. |
| Faktura | `approved` | Zapisana i sprawdzona; historyczny etap przed księgowaniem. |
| Faktura | `posted` | Zaksięgowana, z powiązaniem do księgi. |

`ready` nie oznacza `posted`. Standardowy przycisk w obecnym interfejsie wykonuje zapis i księgowanie razem; starszy endpoint samego zatwierdzenia pozostał w API.

## Budowa aplikacji

Backend to **Python + FastAPI**, uruchamiany przez Uvicorn. Interfejs korzysta z HTML, CSS i JavaScript bez osobnego procesu budowania frontendu. Dane są zapisywane w **PostgreSQL**, a pliki dokumentów w magazynie plikowym.

| Plik / katalog | Odpowiedzialność |
|---|---|
| `run.py` | Uruchomienie strony i poleceń administracyjnych z bezpiecznym odczytem `.env`. |
| `app/main.py` | Endpointy HTTP, logowanie, uprawnienia, przesyłanie dokumentów. |
| `app/db.py` | Połączenia PostgreSQL, dokumenty, kartoteki, zapis faktur i audyt. |
| `app/domain.py` | Walidacja formularza, dat, kwot i pozycji. |
| `app/storage.py` | Dostęp do plików, weryfikacja integralności i pamięć podręczna podglądów. |
| `app/extraction.py` | Rozpoznawanie plików, renderowanie PDF i techniczna obsługa OCR. |
| `app/auto_split.py`, `app/splitting.py` | Wykrywanie układu skanu, wycinanie dokumentów, zapis i cofanie podziału. |
| `app/processing.py`, `app/worker.py` | Kolejka, rezerwacje, ponawianie i proces odczytu przez API. |
| `app/ai.py` | Przygotowanie żądania OpenAI Responses API i walidacja odpowiedzi. |
| `app/mcp_server.py`, `app/mcp_auth.py`, `app/mcp_jobs.py` | Narzędzia MCP, OAuth i zapisywanie propozycji przez zewnętrznego klienta. |
| `app/booking.py`, `app/posting.py` | Wybór kont oraz księgowanie i korekty. |
| `app/ledger.py` | Księga klienta, salda i historia zapisów. |
| `app/inventaris.py` | Plany amortyzacji i zdarzenia utraty. |
| `app/security.py` | Haszowanie haseł i podpisywanie sesji. |
| `app/static/` | Formularz, podgląd, menu, księga, amortyzacja i widok mobilny. |
| `app/migrations/` | Migracje uruchamiane przez `run.py migrate`. |
| `database/` | Bazowy schemat V5 i dodatkowe skrypty poszczególnych funkcji. |
| `tests/`, `docs/` | Testy oraz dokumentacja i raporty wcześniejszych wydań. |

Kolejka znajduje się w PostgreSQL. Projekt nie wymaga Redis ani Celery. Worker API wykonuje zadania kolejno; blokada PostgreSQL dopuszcza jeden aktywny worker dla bazy.

## Baza danych

### Silnik i podział danych

Projekt korzysta z PostgreSQL i sterownika Psycopg 3. **Nie używa SQLite** do logowania ani do księgowości. Połączenie wskazuje `DATABASE_URL` w `.env`.

| Schemat PostgreSQL | Przeznaczenie |
|---|---|
| `core` | Pracownicy, klienci, kontrahenci, rejestracje i lata obrotowe. |
| `documents` | Dokumenty, metadane plików, odczyty, podziały, kolejka i autoryzacja MCP. |
| `accounting` | Konta, kody VAT, faktury, pozycje, księga i rejestr VAT. |
| `assets` | Dane środków trwałych; portal zapisuje własne plany amortyzacji. |
| `tax` | M.in. blokady okresów i struktury deklaracji wykorzystywane przy kontroli zapisów. |
| `banking` | Struktury bankowe i powiązania płatności; bez pełnego interfejsu bankowego w tym projekcie. |
| `payroll` | Struktury płacowe wspólnego modelu; poza zakresem portalu. |
| `audit` | Historia operacji i zmian. |

### Główne tabele i relacje

| Tabela | Co przechowuje / z czym jest powiązana |
|---|---|
| `core.staff_users` | Konto pracownika, hasz hasła, rola i aktywność. |
| `core.clients` | Kartoteka klientów; pole `odoo_partner_id` umożliwia powiązanie z systemem zewnętrznym. |
| `core.client_counterparties` | Kontrahenci przypisani do klienta. |
| `documents.accounting_documents` | Dokument klienta i jego etap w obiegu. |
| `documents.stored_files` | SHA-256, lokalizacja, rozmiar i typ pliku. |
| `documents.accounting_document_files` | Powiązanie dokumentu z plikiem, jego nazwa i rola. |
| `documents.document_ocr_runs` | Kolejne wyniki OCR/AI: surowy wynik, pola i ostrzeżenia. |
| `documents.document_imports`, `document_import_items` | Partia podziału oraz pochodzenie, granice i obrót każdego wycinka. |
| `documents.processing_jobs`, `processing_worker` | Stan kolejki i heartbeat workera. |
| `documents.mcp_grants`, `mcp_codes`, `mcp_claims` | Dostępy OAuth i rezerwacje dokumentów przez MCP. |
| `accounting.accounts` | Plan kont z numerami i typami kont. |
| `accounting.invoice_desk_account_catalog` | Katalog kont dopuszczonych w pozycjach zakupu lub sprzedaży. |
| `accounting.staff_booking_categories`, `booking_category_account_mappings` | Zgodność ze starszym modelem kategorii i przypisań do kont. |
| `accounting.vat_codes` | Techniczny kod VAT, etykieta, stawka, kierunek i okres obowiązywania. |
| `accounting.invoices`, `invoice_lines` | Nagłówek faktury oraz pozycje z kontami i kodami VAT. |
| `accounting.invoice_source_documents` | Powiązanie faktury z właściwym dokumentem źródłowym. |
| `accounting.invoice_validation_checks` | Wyniki kontroli zapisywane przy zatwierdzaniu. |
| `accounting.general_ledger_entries`, `general_ledger_entry_lines` | Nagłówek dekretu oraz jego strony debet/kredyt. |
| `accounting.vat_transactions` | Kwoty podstawy, VAT naliczonego i należnego, powiązane z pozycjami i księgą. |
| `accounting.invoice_desk_corrections` | Poprzednia faktura, nowa faktura, storno i przyczyna poprawki. |
| `assets.invoice_desk_depreciation_plans` | Plan amortyzacji przypisany do pozycji faktury, notatka i ewentualna utrata. |
| `tax.accounting_period_locks`, `core.client_fiscal_years` | Kontrola zamkniętych okresów i lat. |
| `audit.change_log` | Kto wykonał operację, kiedy oraz stan przed/po zmianie; tabela chroniona przed zmianą i usuwaniem historii. |

Relacja robocza to: **klient → dokument → faktura → pozycje → zapis w księdze i VAT**. Plik jest połączony z dokumentem osobną tabelą. Po podziale każdy wycinek otrzymuje własny dokument i może zostać zaksięgowany niezależnie od pozostałych.

Kartoteka klientów jest odczytywana z bazy. Ten projekt nie implementuje samodzielnego synchronizatora Odoo ani edytora klientów; dane muszą zostać dostarczone przez osobny proces lub administrację bazy.

### Pliki, daty i zgodność danych

- Oryginały nie są zapisywane jako bajty w PostgreSQL. Baza zawiera metadane i powiązania; sam plik znajduje się w `DOCUMENT_STORAGE`.
- Nowy plik ma adres zależny od treści: `<pierwsze-2-znaki-SHA>/<SHA>.<rozszerzenie>`. Możliwe jest współdzielenie identycznej treści przy zachowaniu osobnych powiązań klientów.
- `EXISTING_FILE_ROOTS` pozwala udostępnić istniejące lokalne lub zamontowane zasoby. Nie jest klientem Nextcloud ani synchronizatorem plików.
- Kwoty backend zapisuje jako `Decimal` / `numeric`, bez obliczania zapisywanych wartości pieniężnych na `float`.
- Formularz nie ma osobnej **Daty VAT**. Dla nowych zapisów portal wykorzystuje datę wystawienia. Kolumna `vat_date` pozostaje w schemacie V5 dla zgodności; portal nie usuwa jej z bazy i nie przepisuje wcześniejszych rozliczeń.
- Jeden wybór kategorii/konta w formularzu nie oznacza fizycznego scalenia wszystkich starszych tabel. W obecnym katalogu podstawą wyboru jest konto; dawne pola mogą pozostać w historycznych danych.

### Schemat bazowy a aktualizacje

`database/schema_v5.sql` służy do utworzenia **nowej, pustej bazy**. Nie uruchamiaj go ponownie na działającej bazie.

`run.py migrate` wykonuje pliki z `app/migrations/`. Dodatki amortyzacji, księgowania i katalog kont mają również osobne pliki SQL w `database/`; samo `migrate` ich nie uruchamia. Instalator konkretnej aktualizacji może wykonywać przypisane do niej skrypty. Przy ręcznym wdrożeniu trzeba uwzględnić oba zestawy.

## Wymagania

- Python **3.11 lub 3.12** i środowisko wirtualne; obraz Docker używa 3.12.
- PostgreSQL ze schematem V5 i rozszerzeniem `pgcrypto`; dołączone środowisko demonstracyjne używa PostgreSQL 16.
- Tesseract OCR z językami `pol`, `nld`, `eng`, zgodnie z `OCR_LANGUAGES`, dla pomocniczej analizy skanów i ich podziału.
- Dostęp do magazynu dokumentów i prawa zapisu dla użytkownika aplikacji.
- HTTPS i osiągalny adres portalu, jeśli korzysta z niego zewnętrzny klient MCP.
- Jeden skonfigurowany sposób przygotowania danych przez AI: OpenAI API z kluczem albo klient MCP z dostępem do portalu.

Zależności Python są przypięte w `requirements.txt`. Uruchomienie natywne wymaga `requirements-native.txt`, który dodatkowo instaluje `python-dotenv`. Frontend nie wymaga instalowania Node.js ani npm.

Na starym systemie sprawdź wersję interpretera. Systemowy Python 3.7 nie wystarcza. Jeśli istniejąca `.venv` korzysta z Python 3.11, używaj jej zamiast odtwarzać środowisko przez starsze `python3`.

## Uruchomienie bez Dockera

### Już skonfigurowany projekt

Z katalogu aplikacji:

```bash
cd /home/baza_ksiegowosc/invoice-desk
.venv/bin/python run.py web
```

Domyślny adres: **http://127.0.0.1:8001**. Terminal pozostaje otwarty. `run.py` sam wczytuje `.env` bez rozwijania znaków `$` z haseł. Przy działającej usłudze systemowej nie uruchamiaj drugiego procesu na tym samym porcie.

### Pierwsza instalacja natywna

Na współczesnym Ubuntu z Pythonem co najmniej 3.11:

```bash
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-pol tesseract-ocr-nld tesseract-ocr-eng postgresql-client

cd /home/baza_ksiegowosc/invoice-desk
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-native.txt
tesseract --list-langs
```

Jeżeli właściwy interpreter jest dostępny jako `python3.11`, środowisko utwórz przez `python3.11 -m venv .venv`. Nie odtwarzaj istniejącej, działającej `.venv` bez potrzeby.

Konfigurator połączenia:

```bash
.venv/bin/python prepare.py connect
nano .env
chmod 600 .env
```

Konfigurator nie nadpisuje istniejącego `.env`. Przy instalacji natywnej podaj rzeczywisty host PostgreSQL; proponowane `host.docker.internal` dotyczy wariantu kontenerowego. W `.env` ustaw `APP_ORIGIN` dokładnie na adres używany w przeglądarce — np. `http://127.0.0.1:8001` dla lokalnej próby.

Następnie przygotuj bazę i konto pracownika według kolejnej sekcji, skonfiguruj jeden z [trybów AI](#odczyt-dokumentów-przez-ai) i uruchom `run.py web`. W wariancie API uruchom także worker; w wariancie MCP podłącz klienta MCP. Samo uruchomienie strony nie przygotowuje faktur przez AI.

## Przygotowanie bazy i konta pracownika

### Istniejąca baza

Zachowaj obecny `DATABASE_URL`. Przed zmianą struktury wykonaj kopię bazy. Migracje aplikacji uruchamiaj jawnie:

```bash
cd /home/baza_ksiegowosc/invoice-desk
.venv/bin/python run.py migrate
```

Nie zastępuje to instalacji osobnych dodatków SQL, jeżeli brakuje ich w bazie.

### Nowa, pusta baza

Administrator PostgreSQL tworzy bazę i jej właściciela. Przykład **na hoście PostgreSQL**, dla lokalnej nowej instalacji:

```bash
sudo -u postgres createuser --pwprompt invoice_desk
sudo -u postgres createdb --owner=invoice_desk invoice_desk
```

Następnie skonfiguruj `.env` przez `prepare.py connect`, podając te dane. Import bazowego schematu wykonaj tylko do pustej bazy:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import load_dotenv
from app.db import Database
import os

load_dotenv('.env', override=True, interpolate=False)
with Database(os.environ['DATABASE_URL']).connect() as c:
    existing = c.execute("SELECT to_regclass('core.clients') AS name").fetchone()
    if existing['name']:
        raise SystemExit('Baza ma już schemat. Nie uruchomiono importu.')
    c.execute(Path('database/schema_v5.sql').read_text(encoding='utf-8'))
print('Utworzono schemat V5.')
PY

.venv/bin/python run.py migrate
```

Ta komenda nie tworzy kartoteki klientów ani nie podłącza synchronizacji z systemem zewnętrznym.

### Dodatki SQL obecnej wersji

Jeśli funkcje nie zostały już zainstalowane przez ich aktualizatory, przygotuj katalog kont, księgowanie i plany amortyzacji poniższym poleceniem. Wymaga ono wszystkich wymienionych plików w aktualnym projekcie. Brak pliku lub konflikt danych przerywa transakcję.

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import load_dotenv
from app.db import Database
import os

load_dotenv('.env', override=True, interpolate=False)
names = [
    'gnucash_invoice_accounts.sql',
    'invoice_desk_posting.sql',
    'invoice_desk_inventaris.sql',
    'invoice_desk_inventaris_events.sql',
]
scripts = [Path('database', name).read_text(encoding='utf-8') for name in names]
with Database(os.environ['DATABASE_URL']).connect() as c:
    for script in scripts:
        c.execute(script)
print('Zainstalowano/zweryfikowano dodatki bazy.')
PY
```

Katalog kont nie nadpisuje istniejących kont o sprzecznym znaczeniu. Jeśli zgłasza konflikt, porównaj numer, nazwę, typ i aktywność konta zamiast usuwać dane. Skrypt zawiera katalog dostarczony dla tego wdrożenia; przy innym planie kont należy dostosować go świadomie.

### Konto pracownika

`run.py` nie ma podkomendy `create-user`; użyj modułu administracyjnego z wczytaniem `.env`:

```bash
.venv/bin/python -c '
from dotenv import load_dotenv
import runpy
import sys

load_dotenv(".env", override=True, interpolate=False)
sys.argv = ["app.manage", "create-user"]
runpy.run_module("app.manage", run_name="__main__")
'
```

Podaj e-mail, imię, rolę i hasło o długości minimum 12 znaków. Istniejącego konta polecenie nie nadpisuje.

Nadanie istniejącemu księgowemu roli głównego księgowego — adres poniżej jest przykładem do zastąpienia:

```bash
.venv/bin/python -m app.manage grant-chief-accountant ksiegowy@example.org
```

Ta podkomenda sama odczytuje `.env` i przyjmuje aktywne konto z rolą `accountant` lub `chief_accountant`.

## Konfiguracja

Konfiguracja znajduje się w `.env`; nie dodawaj tego pliku, haseł ani dokumentów klientów do repozytorium. Poniższe adresy są przykładami, nie danymi istniejącego serwera.

| Zmienna | Wartość / znaczenie |
|---|---|
| `DATABASE_URL` | Wymagany DSN PostgreSQL; konfigurator koduje znaki specjalne hasła w URL. |
| `APP_SESSION_SECRET` | Wymagany sekret sesji, minimum 32 znaki; generowany przez konfigurator. Zmiana unieważnia dotychczasowe sesje. |
| `DOCUMENT_STORAGE` | Magazyn nowych plików; domyślnie `./data/files`, w Compose `/data/files`. |
| `EXISTING_FILE_ROOTS` | Dodatkowe dozwolone katalogi plików; w Linux rozdzielone dwukropkiem. |
| `APP_ORIGIN` | Dokładny origin przeglądarki, np. `https://faktury.example.org`; bez ścieżki. |
| `COOKIE_SECURE` | `1` dla HTTPS; domyślnie `0`. Przy `1` logowanie przez zwykłe HTTP nie działa poprawnie. |
| `OCR_LANGUAGES` | Domyślnie `pol+nld+eng`; wymaga zainstalowanych danych językowych Tesseract. |
| `BACKGROUND_PROCESSING` | Ustaw `1` w obu opisanych trybach AI. `0` uruchamia starszy odczyt lokalny i nie realizuje opisanego przepływu z AI. |
| `AI_PROVIDER` | `mcp` wybiera MCP; pozostałe wartości korzystają z wyboru `AI_ENABLED`. Konfigurator API zapisuje `openai`. |
| `AI_ENABLED` | Dla API ustaw `1`. W trybie `AI_PROVIDER=mcp` konfigurator ustawia `0`, ponieważ AI działa po stronie klienta MCP. Poza MCP wartość `0` wybiera starszy odczyt lokalny. |
| `OPENAI_API_KEY` | Klucz używany wyłącznie przez tryb API. |
| `OPENAI_MODEL` | Nazwa modelu API; domyślna wartość zapisana w kodzie to `gpt-4.1-mini`. |
| `PROCESSING_POLL_SECONDS` | Worker: odstęp skanowania kolejki; domyślnie 15 s, zakres 5–300 s. |
| `PROCESSING_GAP_SECONDS` | Worker: przerwa po zadaniu; domyślnie 2 s, zakres 1–60 s. Przy błędach może być dłuższa. |
| `MCP_PUBLIC_URL` | Publiczny adres HTTPS tej aplikacji, bez ścieżki; np. `https://faktury.example.org`. |
| `MCP_CLIENT_ID`, `MCP_CLIENT_SECRET` | Dane prywatnego klienta OAuth, tworzone przez `configure_mcp.py`. |
| `MCP_ALLOWED_STAFF_IDS` | Lista identyfikatorów uprawnionych pracowników, rozdzielona przecinkami. |
| `MCP_REDIRECT_URIS` | Dodatkowe adresy powrotu dopuszczone przez implementację OAuth. |
| `APP_DEMO`, `DEMO_SEED`, `DEMO_PASSWORD` | Zmienne osobnego środowiska demonstracyjnego; nie włączaj zasiewania demo w bazie roboczej. |

`run.py` ładuje `.env`, ale samo `python -m app.worker` i większość poleceń `app.manage` tego nie robi. Dlatego poniższe przykłady workera i administracji jawnie odczytują konfigurację.

## Odczyt dokumentów przez AI

W opisanym sposobie pracy **propozycję formularza przygotowuje AI**, łącznie z przypisaniem kont i kodów VAT ze słowników dostępnych w portalu. Pracownik porównuje wynik z oryginałem, poprawia błędy lub braki i potwierdza sprawdzenie. Niepewnych wartości AI nie powinno zgadywać; mogą pozostać do poprawienia.

| Tryb AI | Kto przygotowuje propozycję | Wymagany proces |
|---|---|---|
| OpenAI API | Worker wysyła obrazy i kontekst do modelu przez API, następnie waliduje odpowiedź. | Strona WWW, `app.worker`, klucz API. |
| MCP | Zewnętrzny klient AI pobiera dokumenty i zapisuje propozycje narzędziami MCP. | Stale dostępna strona WWW i podłączony klient MCP; bez `app.worker`. |

Tryb AI jest niezależny od uruchomienia aplikacji z Dockerem lub bez niego. W obu wariantach stosuje się te same reguły weryfikacji i księgowania.

**Rola OCR:** Tesseract pozostaje techniczną zależnością, m.in. do rozpoznawania układu skanu przy podziale. Nie zastępuje AI przygotowującego formularz w opisanym przepływie. Kod zachowuje starsze tryby samego OCR dla zgodności, ale poniższe instrukcje konfigurują pracę z AI i nie wymagają ich włączania. Zmiana dokumentacji nie usuwa tych trybów z kodu.

Odczyt tworzy propozycję do sprawdzenia. Przy zmianie dokumentu w trakcie odczytu kontrola wersji nie pozwala nadpisać aktualnych danych wynikiem starego zadania.

### OpenAI API — konfiguracja bez Dockera

```bash
.venv/bin/python configure_ai.py
.venv/bin/python run.py migrate
```

Konfigurator pobiera klucz bez wyświetlania wpisywanych znaków i ustawia odczyt AI w kolejce. Po konfiguracji zrestartuj stronę. Worker uruchom w drugim terminalu:

```bash
cd /home/baza_ksiegowosc/invoice-desk
.venv/bin/python - <<'PY'
from dotenv import load_dotenv
import runpy

load_dotenv('.env', override=True, interpolate=False)
runpy.run_module('app.worker', run_name='__main__')
PY
```

To osobny tryb korzystający z API; samo połączenie MCP nie dostarcza mu klucza ani uprawnień API. Polecenia kontenerowe opisano w [wariancie Docker](#uruchomienie-w-dockerze).

Implementacja wysyła żądania do `https://api.openai.com/v1/responses`. Model musi być dostępny dla używanego klucza i obsługiwać wymagane wejście oraz wynik strukturalny. Błąd 429 może oznaczać limit lub brak dostępnej kwoty — powtarzanie zadania nie usuwa tej przyczyny. Program ogranicza liczbę prób do trzech i odracza zadania przy błędach przejściowych.

### MCP — konfiguracja bez Dockera

Najpierw zapewnij działający publiczny HTTPS prowadzący do tej aplikacji. Następnie, z katalogu projektu:

```bash
.venv/bin/python configure_mcp.py
.venv/bin/python run.py migrate
.venv/bin/python run.py enable-mcp
```

Przed przełączeniem zatrzymaj worker API lub pozostawiony ze starszego wdrożenia worker OCR. `enable-mcp` sprawdza jego aktywność i przenosi niedokończone zadania do trybu MCP. W tym trybie **nie uruchamiaj `app.worker`**.

Konfigurator zapyta o HTTPS i istniejącego pracownika, zachowa połączenie z bazą oraz wypisze URL `/mcp` i dane OAuth. Sekret wpisuj wyłącznie w konfiguracji klienta. Konfigurator tej wersji akceptuje role `admin`, `accountant`, `reviewer`; mechanizm dostępu serwera uwzględnia również `chief_accountant`, ale sam konfigurator ma węższy warunek wyboru konta.

Klient MCP korzysta z narzędzi:

| Narzędzie | Działanie |
|---|---|
| `invoice_queue_report` | Stan kolejki i ostatnio zmienione dokumenty. |
| `next_invoice` | Rezerwacja jednego dokumentu na 20 minut. |
| `get_invoice_page` | Pobranie obrazu strony zarezerwowanego dokumentu. |
| `save_invoice_proposal` | Zapis propozycji po obejrzeniu wszystkich stron. |
| `release_invoice` | Zwolnienie rezerwacji z informacją o przyczynie. |

Serwer nie udostępnia przez MCP księgowania, usuwania dokumentów ani dowolnych zapytań SQL. `queued` w tym trybie oznacza oczekiwanie na klienta MCP. Portal sam nie wywołuje ChatGPT i nie tworzy harmonogramu; ewentualne zadanie okresowe działa po stronie klienta i musi mieć dostęp do tych narzędzi.

Odebranie wszystkich zapisanych dostępów MCP:

```bash
.venv/bin/python run.py revoke-mcp
```

## Rozdzielanie dokumentów

### Automatyczny podział

`app/auto_split.py` analizuje położenie słów z Tesseract TSV i odstępy na obrazie. Szuka osobnych obszarów zawierających cechy dokumentów, kwoty, a przy pewnym rozpoznaniu także nagłówki, daty i sumy. Obsługuje układ obok siebie, jeden pod drugim i podziały zagnieżdżone. Ten mechanizm jest lokalny i sam nie wywołuje API.

- Nowy JPG/PNG lub jednostronicowy PDF jest analizowany przy przesyłaniu.
- Przy jednoznacznym wyniku tworzone są osobne dokumenty i wycinki PNG. W trybie kolejki każdy trafia do odczytu osobno.
- Przy niepewnym wyniku portal proponuje ramki do sprawdzenia i poprawienia przed zapisaniem.
- Istniejący dokument oznaczony przez odczyt jako zawierający kilka faktur jest analizowany po otwarciu, gdy formularz nie ma niezapisanych zmian.
- Przycisk **Rozdziel automatycznie** pozwala ponowić analizę na żądanie; **Zaznacz ręcznie** zachowuje dotychczasową obsługę.

Wielostronicowy PDF nie jest automatycznie rozdzielany, ponieważ kilka stron może należeć do jednej faktury. Nakładające się, obrócone, mało czytelne dokumenty lub brak wyraźnych odstępów mogą wymagać ręcznego podziału. Wykrycie obszarów nie jest gwarancją poprawności danych księgowych.

### Zapis i cofnięcie

Podział obejmuje 2–20 niepokrywających się obszarów. Ręcznie można wskazać stronę i obrót wycinka. Każdy obszar tworzy osobny dokument z własnym statusem, odczytem i późniejszą fakturą.

Oryginał pozostaje zachowany, dostaje `archived` i znika z roboczej kolejki, żeby nie liczyć go drugi raz. Pochodzenie wycinków zapisują tabele importu. Dla automatycznego podziału partia zawiera dodatkowo metodę i wersję detektora.

Cofnięcie podziału jest możliwe, dopóki wycinki nie mają zapisanych faktur i nie zostały dalej przetworzone. Nie usuwa historii: wycofuje wycinki i przywraca oryginał. Automatyczna analiza nie odtwarza od razu cofniętego podziału; ponowny podział można uruchomić jawnie.

Operacja ma kontrolę wersji i idempotencję. Powtórzenie tego samego zapisu nie tworzy kolejnej partii. Pliki nie są częścią transakcji SQL — po błędzie mogą pozostać niepowiązane pliki, ale nie częściowo zapisany podział w bazie. Nie należy usuwać ich masowo bez sprawdzenia powiązań.

## Księgowanie, korekty i księga klienta

### Konta i VAT

AI proponuje konto i kod VAT dla każdej pozycji. Formularz ma jedno pole kategorii/konta i osobny kod VAT, które pracownik weryfikuje i w razie potrzeby poprawia. Numer konta pomaga wyszukiwać i rozróżniać pozycje. Katalog rozdziela konta zakupowe i sprzedażowe; zmiana konta nie ustawia automatycznie stawki VAT — obie wartości są sprawdzane niezależnie.

| Etykieta w interfejsie | Kod techniczny w bazie |
|---|---|
| VAT 0% | `PORTAL_0` |
| VAT 9% | `PORTAL_9` |
| VAT 21% | `PORTAL_21` |
| VAT zwolniony | `PORTAL_EXEMPT` |

VAT 0% i zwolnienie mają odrębne identyfikatory i znaczenie w modelu. Numery kont oraz identyfikatory kodów są wykorzystywane przez walidację — zmiana samej nazwy wyświetlanej nie zmienia reguł księgowania.

### Jeden zapis do księgi

Endpoint `/book` zapisuje fakturę i księguje ją w jednej transakcji. Niepowodzenie któregokolwiek etapu wycofuje całą operację. Kontrolowane są m.in. kwoty pozycji, netto + VAT = brutto, aktywność kont, kierunek i ważność VAT oraz zamknięte okresy.

| Typ | Podstawowy schemat dekretu |
|---|---|
| Zakup | Debet wybranego konta, debet `150 Te vorderen BTW` dla odliczanego VAT, kredyt `140 Crediteuren` kwotą brutto. Nieodliczony VAT zwiększa wartość na koncie pozycji. |
| Sprzedaż | Debet `130 Debiteuren` kwotą brutto, kredyt konta przychodu i `151 Te betalen BTW` dla VAT należnego. |

Zapisy trafiają do dziennika `IDESK`. Konta rozrachunkowe i VAT są używane przez silnik, a nie wybierane jako zwykłe konta pozycji. Muszą istnieć z odpowiednim numerem, nazwą, typem i uprawnieniem do księgowania.

Aktualny silnik przyjmuje `invoice_kind='invoice'`, dodatnie brutto, EUR i zakup/sprzedaż. Odrzuca zagraniczne prefiksy numerów VAT, konta specjalne `460`, `490`, `840`, `860` oraz kody VAT spoza obsługiwanego zestawu. **Rozdzielenie paragonu i przygotowanie jego danych nie oznacza obsługi paragonu jako odrębnego typu księgowania.** Formularz nie wykonuje sprawdzenia numerów VAT w VIES.

### Duplikaty

1. **Przy przesyłaniu:** identyczny SHA-256 pliku u tego samego klienta zwraca istniejący dokument; dla rozdzielonego oryginału zwracane są jego wycinki.
2. **Przy podziale:** powtórzenie tej samej operacji nie tworzy kolejnych dokumentów; identyczne wycinki w jednej partii są odrzucane.
3. **Przy zapisie faktury:** sprawdzany jest znormalizowany numer, klient, typ, rodzaj i rok faktury oraz dane kontrahenta. Klucz importu i blokady transakcyjne chronią również przed ponownym zapisem tego samego dokumentu.

Inne zdjęcie tej samej faktury może mieć inny SHA-256. Wtedy ochronę zapewniają dane faktury, a nie sam plik. Dokumenty bez numeru wymagają szczególnej kontroli pracownika.

### Poprawienie zaksięgowanej faktury

Funkcja jest dostępna dla `admin` i `chief_accountant`. Wymaga przyczyny, aktualnej wersji i otwartego okresu. Tworzy storno poprzedniego dekretu oraz nową fakturę i dekret; nie nadpisuje zaksięgowanych pozycji.

Poprawka jest ograniczona do zapisów zaksięgowanych w tym portalu. Powiązanie z rozliczoną płatnością, deklaracją VAT lub właściwą kartoteką środków trwałych może zablokować prostą korektę. Rejestr VAT dla otwartego okresu jest zastępowany transakcyjnie z zachowaniem audytu poprzednich danych.

Plany amortyzacji mogą zostać przeniesione na nową fakturę, jeśli odpowiadające im pozycje nie zmieniły się w istotnych polach. Zmienione pozycje mogą wymagać ponownego sprawdzenia planów.

### Księga klienta

Zakładka pokazuje zapisy według daty księgowania, ze stronicowaniem po 30. Dla korekt wykonanych przez portal lista główna pokazuje aktualny zapis, a poprzednie zapisy i storna są dostępne w historii. Szczegóły pozwalają sprawdzić konta oraz otworzyć fakturę.

**Debet (Wn)** i **Kredyt (Ma)** to strony zapisu; w zbilansowanym dekrecie ich sumy są równe. **Debiteuren** to należności na koncie 130, a **Crediteuren** — zobowiązania na koncie 140. Nie są to inne nazwy stron Wn/Ma.

Pola Debiteuren/Crediteuren u góry pokazują narastające salda do końca wybranego okresu, z uwzględnieniem zapisanych w księdze płatności, otwarć i storn. Nie są sumą brutto faktur widocznych na bieżącej stronie ani wyłącznie obrotem między datą początkową i końcową.

## Amortyzacja

Zakładka jest dostępna u klienta mającego dodatnią pozycję zakupu Inventaris na koncie `002`, z faktury zatwierdzonej lub zaksięgowanej. Dla danych starszego modelu istnieje zgodność z kategorią Inventaris bez wskazanego konta. Pozycje zastąpione stornem nie są bieżącym źródłem nowego planu.

Plan obejmuje nazwę, datę przyjęcia do używania, wartość początkową, wartość końcową, okres **1–600 miesięcy**, notatkę do 1000 znaków oraz opcjonalny rodzaj i datę utraty.

Program oblicza plan liniowy, pokazuje sumy roczne i odpisy miesięczne. Niepełne miesiące liczy proporcjonalnie do dni, a zaokrąglenia rozlicza narastająco do groszy. Przy kradzieży (`stolen`) lub zgubieniu (`lost`) plan zatrzymuje odpisy od daty utraty i pokazuje pozostałą wartość.

**To plan, nie automatyczne księgowanie odpisów.** Moduł nie tworzy z tych danych zapisów w księdze, wpisów do `assets.fixed_asset_depreciation_entries`, rozliczenia utraty ani roszczenia ubezpieczeniowego. Dobór okresu, wartości i sposobu ujęcia utraty pozostaje do sprawdzenia przez księgowego. Zapis planu ma numer rewizji i audyt.

## Role i zabezpieczenia

| Rola | Odczyt | Dodawanie, podział, propozycje, pierwsze księgowanie i plan amortyzacji | Poprawka faktury `posted` |
|---|---|---|---|
| `read_only` | Tak | Nie | Nie |
| `reviewer` | Tak | Tak | Nie |
| `accountant` | Tak | Tak | Nie |
| `chief_accountant` | Tak | Tak | Tak |
| `admin` | Tak | Tak | Tak |

Tabela odzwierciedla bieżący kod: `reviewer` nie jest rolą tylko do oglądania. Model uprawnień nie ogranicza pracownika do przydzielonej listy klientów; aktywne konto ma dostęp do kartoteki dostępnej w portalu. MCP dodatkowo wymaga dopuszczenia identyfikatora pracownika i zgody OAuth.

Hasła są haszowane PBKDF2-SHA256 z indywidualną solą i 1000000 iteracji. Sesja jest podpisana, ważna do 8 godzin, w ciasteczku HttpOnly i SameSite Strict; dla HTTPS należy włączyć Secure. Operacje zapisu wymagają tokenu CSRF i podlegają kontroli originu. Aktywność konta jest sprawdzana przy żądaniach. Limit prób logowania jest przechowywany w pamięci procesu.

Dostęp do oryginałów i podglądów wymaga logowania. Pliki są sprawdzane według typu, rozmiaru, dozwolonego katalogu i SHA-256. PostgreSQL używa transakcji, blokad oraz triggerów chroniących zaksięgowane dane i audyt. Kontrola `updated_at`/rewizji wykrywa równoległe zmiany.

Te mechanizmy nie zastępują poprawnej konfiguracji serwera, kopii zapasowych i kontroli dostępu do PostgreSQL oraz katalogu dokumentów.

## Usługa systemowa i HTTPS

### Systemd — przykład dla lokalnego proxy

Przykład zakłada zwykłego użytkownika systemowego `baza_ksiegowosc` i katalog `/home/baza_ksiegowosc/invoice-desk`. Nazwę użytkownika i grupy dostosuj do faktycznego właściciela plików. **Nie zastępuj istniejącej działającej usługi bez porównania ustawień.**

Plik `/etc/systemd/system/invoice-desk.service`:

```ini
[Unit]
Description=Invoice Desk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=baza_ksiegowosc
Group=baza_ksiegowosc
WorkingDirectory=/home/baza_ksiegowosc/invoice-desk
ExecStart=/home/baza_ksiegowosc/invoice-desk/.venv/bin/python /home/baza_ksiegowosc/invoice-desk/run.py web
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
UMask=0077

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now invoice-desk
sudo systemctl status invoice-desk --no-pager
```

Usługa powyżej uruchamia wyłącznie WWW. Dla AI przez API potrzebny jest również worker uruchamiany z tą samą konfiguracją i magazynem; dla MCP nie należy go dodawać.

### Reverse proxy

`run.py web` nasłuchuje na **127.0.0.1 serwera aplikacji**. Ten adres jest osiągalny dla proxy na tym samym hoście. Przykład Caddy z przykładową domeną:

```caddyfile
faktury.example.org {
    reverse_proxy 127.0.0.1:8001
}
```

Jeżeli Caddy działa na innym serwerze lub w kontenerze, jego `127.0.0.1` nie wskazuje serwera aplikacji. Proxy musi kierować do osiągalnego adresu LAN, a Uvicorn nasłuchiwać na tym interfejsie. Dla układu z serwerem aplikacji `192.168.1.123` przykładowy upstream to `192.168.1.123:8001`; zwykłe `run.py web` nie udostępnia tego interfejsu.

Do własnej usługi można wtedy użyć `EnvironmentFile=/home/baza_ksiegowosc/invoice-desk/.env` oraz zastąpić jej `ExecStart` poleceniem:

```ini
ExecStart=/home/baza_ksiegowosc/invoice-desk/.venv/bin/python -m uvicorn app.main:create_app --factory --host 192.168.1.123 --port 8001 --no-access-log
```

W tym wariancie `.env` dostarcza systemd, bo Uvicorn sam go nie odczytuje. Port backendu ogranicz sieciowo do potrzebnych hostów proxy. Dla publicznego adresu ustaw `APP_ORIGIN` na ten HTTPS i `COOKIE_SECURE=1`.

Proxy powinno przekazywać całą aplikację, w tym `/mcp`, `/oauth/` i `/.well-known/`, zachowywać Host i informację o HTTPS oraz dopuszczać przesyłkę co najmniej 26 MiB. Nie dodawaj przed endpointami MCP osobnej strony logowania proxy. Nie loguj kodów i tokenów OAuth. Przy Caddy w Dockerze konfigurację zmienia się w pliku zamontowanym do kontenera; `/etc/caddy/Caddyfile` na hoście nie musi istnieć.

## Uruchomienie w Dockerze

To drugi wariant uruchomienia tej samej aplikacji. Nie uruchamiaj jednocześnie usługi natywnej i Compose na tym samym porcie. Potrzebny jest działający Docker Engine zgodny z systemem/cgroups i plugin Compose. PostgreSQL może nadal działać na osobnym serwerze.

### 1. Baza i konfiguracja wspólna

Przygotuj bazę V5, dodatki SQL i konto pracownika zgodnie z [sekcją bazy](#przygotowanie-bazy-i-konta-pracownika). Jeśli masz już skonfigurowany portal, zachowaj jego bazę i `.env`.

W świeżym projekcie konfigurację połączenia można utworzyć na hoście poleceniem:

```bash
cd /home/baza_ksiegowosc/invoice-desk
python3 prepare.py connect
```

`prepare.py` używa biblioteki standardowej Pythona; nie uruchamia aplikacji i nie wymaga natywnej `.venv`. Nie nadpisuje istniejącego `.env`. Skonfiguruj `APP_ORIGIN` i `COOKIE_SECURE` zgodnie z adresem używanym przez przeglądarkę.

Host bazy w `DATABASE_URL` musi być osiągalny **z kontenera** — `127.0.0.1` oznacza w nim sam kontener. `compose.yml` nie tworzy serwera PostgreSQL. Dla bazy na hoście Dockera przewidziano nazwę `host.docker.internal`; dla osobnego serwera podaj jego adres.

Zbuduj obraz:

```bash
docker compose -f compose.yml build
```

**Tylko dla nowej, pustej bazy**, po utworzeniu jej i użytkownika przez administratora PostgreSQL, można zaimportować bazowy schemat z kontenera. Dla istniejącej bazy pomiń ten krok:

```bash
docker compose -f compose.yml run --rm --no-deps -T \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/database:/schema:ro" \
  app python -c '
from pathlib import Path
from app.db import Database
import os

with Database(os.environ["DATABASE_URL"]).connect() as c:
    existing = c.execute("SELECT to_regclass(%s) AS name", ("core.clients",)).fetchone()
    if existing["name"]:
        raise SystemExit("Baza ma już schemat. Nie uruchomiono importu.")
    c.execute(Path("/schema/schema_v5.sql").read_text(encoding="utf-8"))
print("Utworzono schemat V5.")
'
```

Migracje aplikacji można wykonać z obrazu, bez natywnego Pythona aplikacji na hoście:

```bash
docker compose -f compose.yml run --rm --no-deps app python -m app.manage migrate
```

Osobne dodatki SQL nie są uruchamiane przez `migrate`. Obecny Dockerfile nie kopiuje katalogu `database/`. Gdy wdrażasz je z kontenera zamiast poleceniem natywnym, udostępnij katalog i uruchom skrypty w jednej transakcji:

```bash
docker compose -f compose.yml run --rm --no-deps -T \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/database:/schema:ro" \
  app python -c '
from pathlib import Path
from app.db import Database
import os

names = ["gnucash_invoice_accounts.sql", "invoice_desk_posting.sql",
         "invoice_desk_inventaris.sql", "invoice_desk_inventaris_events.sql"]
scripts = [Path("/schema", name).read_text(encoding="utf-8") for name in names]
with Database(os.environ["DATABASE_URL"]).connect() as c:
    for script in scripts:
        c.execute(script)
print("Zainstalowano/zweryfikowano dodatki bazy.")
'
```

Wykonuj instalację dodatków po kopii bazy i zgodnie z jej aktualnym stanem; nie importuj ponownie bazowego `schema_v5.sql` do działającej bazy. Polecenie wymaga uprawnień do Docker Engine i odczytu wskazanych plików SQL.

Jeśli potrzebujesz utworzyć konto pracownika, zrób to po przygotowaniu schematu i dodatków:

```bash
docker compose -f compose.yml run --rm --no-deps app python -m app.manage create-user
```

### 2. Wybierz jeden sposób pracy AI

Jeśli `.env` ma już właściwą konfigurację AI, nie uruchamiaj konfiguratora ponownie — przejdź do startu wybranego wariantu.

Przy przełączaniu z Docker + API na Docker + MCP najpierw zatrzymaj poprzedni zestaw poleceniem `docker compose -f compose.yml -f compose.background.yml stop`, aby nie pozostawić workera API. Wybieraj konfigurację i zestaw Compose świadomie; nie uruchamiaj obu wariantów odczytu naraz.

#### Wariant Docker + MCP

Wymagane są publiczny HTTPS tego portalu i istniejące konto pracownika. Konfigurator można uruchomić w jednorazowym kontenerze. Montuje katalog projektu, żeby zapisać jego `.env`, i instaluje pomocniczy `python-dotenv` wyłącznie w `/tmp` tego kontenera:

```bash
docker compose -f compose.yml run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/project" --workdir /project \
  app sh -c 'python -m pip install --no-cache-dir --disable-pip-version-check --target /tmp/invoice-tools python-dotenv==1.2.1 && PYTHONPATH=/tmp/invoice-tools python configure_mcp.py'

docker compose -f compose.yml run --rm --no-deps app python -m app.manage enable-mcp
docker compose -f compose.yml up -d
```

Konfigurator ustawia `BACKGROUND_PROCESSING=1`, `AI_PROVIDER=mcp`, `AI_ENABLED=0` oraz dane HTTPS/OAuth. Zapisuj sekret tylko w konfiguracji klienta MCP. Połącz klienta z adresem `/mcp` i sprawdź odczyt jednej faktury. W tym wariancie **nie uruchamiaj `compose.background.yml` ani `app.worker`**: AI przygotowuje propozycje przez klienta MCP.

#### Wariant Docker + OpenAI API

Jeśli klucz nie został jeszcze skonfigurowany, uruchom konfigurator w jednorazowym kontenerze:

```bash
docker compose -f compose.yml run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/project" --workdir /project \
  app python configure_ai.py

docker compose -f compose.yml -f compose.background.yml build
docker compose -f compose.yml -f compose.background.yml up -d
```

Ten wariant ustawia `BACKGROUND_PROCESSING=1`, `AI_PROVIDER=openai`, `AI_ENABLED=1` i używa `OPENAI_API_KEY`. `compose.background.yml` uruchamia WWW oraz worker API z tym samym magazynem dokumentów. Przetwarzanie odbywa się kolejno w tle.

### 3. Sprawdzenie i obsługa

```bash
docker compose -f compose.yml ps
docker compose -f compose.yml logs --tail=100 app
```

Dla wariantu API sprawdź również worker:

```bash
docker compose -f compose.yml -f compose.background.yml logs --tail=100 worker
```

Po zmianie `.env` odtwórz kontenery przez odpowiednie `up -d`, aby pobrały nowe zmienne. Po zmianie kodu użyj odpowiedniego `up --build -d`. Sam restart istniejącego kontenera nie aktualizuje obrazu i nie pobiera nowego zestawu zmiennych z Compose.

### Port, pliki i przełączenie z instalacji natywnej

Domyślny adres aplikacji to **http://127.0.0.1:8001** na hoście Dockera. W kontenerze Uvicorn nasłuchuje na porcie 8000, a Compose mapuje go na loopback hosta. Jeśli reverse proxy jest na innym serwerze, dostosuj mapowanie do osiągalnego adresu LAN, np. `192.168.1.123:8001:8000`, i ogranicz dostęp do backendu do potrzebnych hostów proxy.

Pliki aplikacji są zapisywane w `/data/files` na wolumenie `invoice_files`. **Przełączenie istniejącej instalacji z `.venv` na Docker nie przenosi dokumentów automatycznie.** Udostępnij ten sam magazyn przez właściwy mount albo przenieś pełną zawartość do wolumenu, zachowując strukturę ścieżek i bazę. Oba procesy wariantu API muszą widzieć te same pliki. Użytkownik obrazu ma UID `10001`; zapewnij mu wymagane prawa do magazynu.

Zwykłe zatrzymanie kontenerów nie usuwa wolumenów. Nie używaj `down -v` jako sposobu aktualizacji — usuwa wolumeny projektu. Kopia bazy nie zawiera plików zapisanych na wolumenie.

### Osobne demo

W osobnej kopii projektu:

```bash
python3 prepare.py demo
docker compose --env-file .env.demo -f compose.demo.yml up --build -d
```

Konfigurator wypisuje dane logowania; adres demo to `http://localhost:8000`. To **starsza konfiguracja demonstracyjna**, z lokalnym OCR zamiast opisanego przepływu AI. Tworzy bazowy V5 i przykładowe dane, ale nie instaluje automatycznie późniejszych dodatków księgi, korekt, katalogu i amortyzacji. Do testowania pełnej obecnej pracy przygotuj osobną bazę z dodatkami i skonfiguruj jeden z trybów AI. Demo nie powinno wskazywać bazy roboczej.

## Aktualizacje i kopie zapasowe

### Aktualizacja istniejącego katalogu

Zachowuj `.env`, `.venv` i dokumenty. Paczki aktualizacji mogą być wykonywalnymi ZIP-ami; uruchamiaj je zgodnie z instrukcją danego wydania, z katalogu działającego projektu. Nie rozpakowuj całego starego wydania na nowszy kod z późniejszymi poprawkami.

Przykład dla paczki automatycznego podziału zapisanej obok katalogu projektu:

```bash
cd /home/baza_ksiegowosc/invoice-desk
.venv/bin/python ../invoice-desk-auto-podzial.zip && sudo systemctl restart invoice-desk
```

Ta konkretna paczka podmienia potrzebne pliki, tworzy kopię poprzedniego kodu i nie wymaga migracji bazy. Inne paczki mogą mieć inne kroki. Kopia plików wykonywana przez instalator **nie jest kopią bazy ani dokumentów**.

Po zmianach interfejsu odśwież stronę przez **Ctrl+F5**. Zmiany `.env` i backendu wymagają restartu właściwych procesów. Przy migracjach zaplanuj przerwę w zapisach i sprawdź wynik przed wznowieniem pracy.

### Co trzeba zabezpieczyć

Kopia odtwarzalnego systemu obejmuje łącznie:

1. Zrzut całej bazy PostgreSQL, z audytem, kolejką i powiązaniami.
2. Magazyn `DOCUMENT_STORAGE` i używane zasoby z `EXISTING_FILE_ROOTS`.
3. `.env` oraz konfiguracje usługi i reverse proxy, przechowywane z ograniczonym dostępem.
4. Wersję kodu i paczki aktualizacji potrzebne do jej odtworzenia.

Zrzut PostgreSQL wykonuj narzędziem `pg_dump` w formacie custom (`-Fc`). Dla spójnego odtworzenia bazy i magazynu wstrzymaj na czas kopii nowe uploady, podziały i inne zapisy. Kopie przechowuj także poza serwerem aplikacji i sprawdzaj odtwarzanie na osobnej bazie.

Poniżej przykład zrzutu bazy z parametrami odczytanymi z `.env`, bez umieszczania hasła w argumentach polecenia:

```bash
.venv/bin/python - <<'PY'
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
import os
import subprocess

os.umask(0o077)
config = dotenv_values('.env', interpolate=False)
params = conninfo_to_dict(config['DATABASE_URL'])
env = os.environ.copy()
mapping = {
    'host': 'PGHOST', 'hostaddr': 'PGHOSTADDR', 'port': 'PGPORT',
    'dbname': 'PGDATABASE', 'user': 'PGUSER', 'password': 'PGPASSWORD',
    'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
    'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY',
    'service': 'PGSERVICE', 'connect_timeout': 'PGCONNECT_TIMEOUT',
}
unknown = set(params) - set(mapping)
if unknown:
    raise SystemExit('DSN ma dodatkowe opcje; uwzględnij je w procedurze kopii: ' + ', '.join(sorted(unknown)))
env.update({mapping[k]: str(v) for k, v in params.items()})
folder = Path('backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('database-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.dump')
subprocess.run(['pg_dump', '--format=custom', '--file', str(target)], env=env, check=True)
print('Zapisano:', target)
PY
```

To polecenie tworzy **tylko zrzut bazy**. Pliki, konfigurację i kod zabezpiecz osobno. Przy odtwarzaniu najpierw przywróć zrzut do nowej bazy, odtwórz magazyn z pasującymi ścieżkami i dopiero wtedy ustaw testowe `DATABASE_URL`. Nie testuj odtworzenia przez nadpisywanie działającej bazy.

## Diagnostyka i testy

### Podstawowe sprawdzenie

```bash
cd /home/baza_ksiegowosc/invoice-desk
.venv/bin/python --version
tesseract --list-langs
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/
sudo systemctl status invoice-desk --no-pager
sudo journalctl -u invoice-desk -n 100 --no-pager
```

HTTP 200 dla `/` potwierdza dostarczenie strony, ale nie przetestuje logowania, zapisu faktury ani odczytu MCP. Jeśli Uvicorn nasłuchuje wyłącznie na LAN zamiast loopback, użyj jego faktycznego adresu w `curl`.

Odczytowe sprawdzenie połączenia i stanu kolejki:

```bash
.venv/bin/python - <<'PY'
from dotenv import load_dotenv
from app.db import Database
import os

load_dotenv('.env', override=True, interpolate=False)
with Database(os.environ['DATABASE_URL']).connect() as c:
    print(c.execute('SELECT current_database() AS database, version() AS version').fetchone())
    table = c.execute("SELECT to_regclass('documents.processing_jobs') AS name").fetchone()
    if table['name']:
        for row in c.execute('SELECT mode, status, count(*) AS count FROM documents.processing_jobs GROUP BY mode, status ORDER BY mode, status'):
            print(row)
PY
```

| Objaw | Co sprawdzić |
|---|---|
| Brak połączenia z bazą | Host, port, SSL, użytkownik i dostępność PostgreSQL; nie wklejaj hasła z DSN do zgłoszenia. |
| Brak tabel / `relation does not exist` | Właściwa baza, migracje `app/migrations/` oraz osobne dodatki SQL. |
| Pusta lista klientów | Aktywne rekordy w `core.clients`; synchronizacja nie jest wykonywana przez ten portal. |
| Brak kont lub VAT | Katalog kont, aktywność, typy, kierunek i ważność kodów; konflikty skryptu katalogu. |
| Kolejka stoi w `queued` | Dla AI przez API: worker i heartbeat. Dla MCP: połączenie klienta, zgoda i uruchomienie zadania po jego stronie. |
| Skoki `processing` → `queued` | Błąd odczytu, wygasła rezerwacja lub odroczenie. Sprawdź komunikat zadania. |
| API zwraca 429 | Przyczynę limitu/kwoty po stronie API; po naprawie ponów nieudane odczyty. |
| Logowanie nie utrzymuje sesji | HTTP przy `COOKIE_SECURE=1`, niezgodny `APP_ORIGIN`, zmieniony sekret lub niewłaściwy adres portalu. |
| Konflikt wersji 409 | Dokument zmienił się w innej sesji lub przez odczyt. Otwórz aktualne dane przed zapisem. |
| Podział nie jest pewny | Czytelność, odstępy, kompletność dokumentów i języki Tesseract; użyj proponowanych ramek lub zaznaczenia ręcznego. |
| Usługa nie startuje / port zajęty | Drugi Uvicorn na tym samym porcie, interpreter `.venv`, prawa do plików i konfiguracja usługi. |
| Stary wygląd po aktualizacji | Ctrl+F5 i zgodność podmienionych plików z resztą wdrożenia. |

### Limity wejścia

- Jeden plik: do **25 MiB**; kontrola całej przesyłki HTTP dopuszcza do **26 MiB**.
- PDF: **1–20 stron**, bez hasła.
- Obraz: JPG/PNG, do **35 milionów pikseli**.
- Podział: **2–20** niepokrywających się obszarów.
- Lista dokumentów API: partie po 50; księga po 30; lista amortyzacji po 50.

### Testy

Testy bazy uruchamiaj tylko na osobnej bazie, np. `invoice_desk_test`, z pełnym schematem i dodatkami. Testy tworzą dane i mogą zmieniać strukturę pomocniczą. Nigdy nie ustawiaj `TEST_DATABASE_URL` na bazę roboczą.

```bash
.venv/bin/python -m pip install -r requirements-test.txt
read -r -s -p 'DSN osobnej bazy invoice_desk_test: ' TEST_DATABASE_URL
echo
export TEST_DATABASE_URL
.venv/bin/python -m pytest tests
unset TEST_DATABASE_URL
```

Obszary do sprawdzania po zmianach: zapis i duplikaty, uprawnienia, konflikt wersji, podział i jego cofnięcie, niezależność wycinków, księgowanie i korekty, historia, plany amortyzacji oraz widoki telefonu i komputera.

Testy i raporty z wcześniejszych wydań opisują ówczesne zachowanie. Po późniejszych poprawkach, szczególnie po włączeniu automatycznego podziału zamiast wyłącznie ręcznego, starsze scenariusze mogą wymagać dostosowania. Nie traktuj historycznego raportu jako potwierdzenia pełnego testu bieżącego wdrożenia. Integrację HTTPS/MCP i zewnętrzny odczyt należy sprawdzić osobno na docelowym środowisku.

## Główne endpointy

Wszystkie operacje na danych wymagają odpowiednich uprawnień; zapis wymaga również CSRF. Poniższa tabela jest mapą API, nie zachętą do omijania formularza i jego walidacji.

| Metoda i ścieżka | Funkcja |
|---|---|
| `POST /api/login`, `POST /api/logout`, `GET /api/me` | Sesja pracownika. |
| `GET /api/clients`, `GET /api/references` | Klienci i słowniki formularza. |
| `GET /api/documents` | Lista dokumentów z filtrami. |
| `POST /api/documents/upload` | Dodanie pliku i próba automatycznego podziału. |
| `GET /api/documents/{id}` | Dokument, pliki, propozycja i aktualna faktura. |
| `GET /api/documents/{id}/file`, `/pages`, `/preview/{page}` | Oryginał, metadane stron i podgląd. |
| `POST /api/documents/{id}/auto-split` | Lokalna analiza i zapis pewnego podziału albo propozycja ramek. |
| `POST /api/documents/{id}/split`, `/unsplit` | Zapis ręcznego podziału i jego cofnięcie. |
| `GET /api/processing`, `GET /api/documents/{id}/preparation` | Stan kolejki i zadania. |
| `POST /api/processing/retry`, `POST /api/documents/{id}/prepare` | Ponowienie odczytów. |
| `POST /api/documents/{id}/book` | Zapis i księgowanie; dla uprawnionych także poprawka faktury. |
| `POST /api/documents/{id}/approve` | Zachowany starszy zapis samego zatwierdzenia; nie wykonuje księgowania. |
| `PUT /api/booking-mappings/{category_id}` | Zgodność ze starszymi powiązaniami kategorii i kont klienta. |
| `GET /api/ledger`, `/api/ledger/{id}`, `/api/ledger/{id}/history` | Księga, szczegóły i historia. |
| `GET /api/inventaris`, `GET/PUT /api/inventaris/{line_id}` | Pozycje Inventaris i plany amortyzacji. |
| `/mcp`, `/oauth/…`, `/.well-known/…` | Narzędzia MCP i autoryzacja; udostępniane w trybie MCP. |

## Informacja o projekcie

Projekt został przygotowany dla firmy **TwojDoradca.nl**.
