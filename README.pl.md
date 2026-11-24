# token-audit

[English](README.md) | **Polski**

Skill do Claude Code, który analizuje zużycie tokenów w Twoich wcześniejszych sesjach Claude Code w danym projekcie i wskazuje, co zmienić, żeby kolejne sesje były tańsze: nieużywane pluginy i serwery MCP, zbyt długie sesje, kosztowne wyniki narzędzi, drogie subagenty i inne. Każda rekomendacja zawiera liczby, na których się opiera, oraz szacowaną oszczędność.

Audyt czyta wyłącznie Twoje własne zapisy sesji z katalogu `~/.claude/projects/` na Twoim komputerze. Nic nie jest nigdzie wysyłane, a skill niczego nie zmienia, dopóki nie wybierzesz rekomendacji.

## Wymagania

- Claude Code
- Python 3 dostępny w `PATH` (polecenie `python --version` powinno działać)

## Instalacja

### Jako plugin (zalecane)

W Claude Code uruchom:

```
/plugin marketplace add kolejniczak/token-audit
/plugin install token-audit@token-audit
```

Następnie uruchom Claude Code ponownie.

Aktualizacja: `/plugin marketplace update token-audit`. Usunięcie: `/plugin uninstall token-audit@token-audit`.

### Dla całego zespołu, przez projekt

Aby Claude Code proponował instalację pluginu każdemu, kto otworzy dane repozytorium, dodaj do commitowanego pliku `.claude/settings.json` tego repozytorium:

```json
{
  "extraKnownMarketplaces": {
    "token-audit": {
      "source": { "source": "github", "repo": "kolejniczak/token-audit" }
    }
  },
  "enabledPlugins": {
    "token-audit@token-audit": true
  }
}
```

### Ręcznie, jako osobisty skill

Skopiuj folder `plugins/token-audit/skills/token-audit` do `~/.claude/skills/token-audit`. Skill wywołujesz wtedy jako `/token-audit` zamiast `/token-audit:token-audit`.

## Użycie

Otwórz Claude Code w projekcie, który chcesz przeanalizować, i uruchom skill. Analizuje on sesje bieżącego projektu.

| Polecenie | Działanie |
|---|---|
| `/token-audit:token-audit --days 7` | Analizuje ostatnie 7 dni |
| `/token-audit:token-audit --since 2026-11-01 --until 2026-11-15` | Analizuje zakres dat (UTC; sama data w `--until` obejmuje cały ten dzień) |
| `/token-audit:token-audit --session <id-sesji>` | Analizuje jedną sesję |
| `/token-audit:token-audit verify <zmiana> <data>` | Sprawdza, czy zmiana wprowadzona tego dnia się opłaciła, porównując okres przed nią i po niej |

Możesz dopisać zwykłymi słowami, na czym się skupić, np. `/token-audit:token-audit --days 14 subagenty`.

### Co otrzymujesz

1. **Raport** z wybranego okresu: sumy i podział kosztów, modele, najdroższe sesje, kontekst startowy, wyniki narzędzi uszeregowane według kosztu, eksplorację przed pierwszą edycją, powtórne odczyty plików, subagenty oraz używane serwery MCP i skille.
2. **Główne źródła kosztów**, każde z przyczyną.
3. **Rekomendacje** uszeregowane według szacowanej oszczędności, każda z dowodami, konkretną zmianą i wyliczeniem oszczędności.

Nic nie zostaje zastosowane, dopóki nie wybierzesz rekomendacji.

### Jak czytać liczby

Koszty podawane są w **ITE** (input-token equivalents, czyli równoważnikach tokenów wejściowych): każdy rodzaj tokenów ma wagę względem niecache'owanego tokenu wejściowego (wejście 1, zapis do cache 1,25 lub 2, odczyt z cache 0,1, wyjście 5). Traktuj ITE jako koszt względny do porównywania zmian, a nie jako rachunek.

### Uruchomienie samego skryptu

Skrypt agregujący działa też bez Claude Code:

```
python plugins/token-audit/skills/token-audit/scripts/token_audit.py --cwd "<ścieżka projektu>" --days 7
```

Uruchom go z `--help`, aby zobaczyć wszystkie opcje.
