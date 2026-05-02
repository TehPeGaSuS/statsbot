# Translating Statsbot

**Português** · **Français** · **Italiano** — see below for your language.

---

## English

### How it works

Statsbot uses standard PO files (`locale/<lang>.po`). Each entry looks like:

```
msgid "{nick} is a very aggressive person. They attacked others {count} times."
msgstr "{nick} é uma pessoa muito agressiva. Atacou outros {count} vezes."
```

The `msgid` is the English string — **do not translate or modify `msgid` lines**.
Only edit the `msgstr` line. `en_US.po` has empty `msgstr` throughout and serves
as the reference catalogue; do not add translations to it.

### Adding a new language

1. Copy `locale/en_US.po` to `locale/<lang>.po` (e.g. `locale/de_DE.po`)
2. Update the `Language:` header
3. Add your language code to `SUPPORTED` in `i18n.py`
4. Translate every `msgstr` line

### Rules

**Must keep — these will break the page if removed or renamed:**

- `{nick}` — IRC nickname, inserted by the bot
- `{nick2}` — second IRC nickname (used in follower strings)
- `{count}` — a number
- `{pct}` — a percentage value (already formatted, e.g. `27.3`)
- `{avg}` — an average value
- `{n}` — a number (days, nicks, etc.)
- `{channel}` — channel name (e.g. `#portugal`)
- `{network}` — network name (e.g. `PTirc`)
- `{url}` — a URL
- `{list}` — a formatted list
- `{date}` — a formatted date string
- `{time}` — a formatted time string
- `{by}` — attribution string (may be empty)
- `{quote}` — a random quote
- `{total}` — total nick count

Placeholders can be **reordered** within the sentence to fit your language's
grammar. You cannot rename them, remove them, or add new ones.

**Must keep — these are not translatable:**

- The four time-band labels: `0-5`, `6-11`, `12-17`, `18-23` — leave as-is.
- `Karma` — proper noun, used as a section title and in the DB.
- `Nick` (column header) — IRC terminology, universally understood.
- `URL`, `Smiley` — universally understood abbreviations.
- `:-)`  `:)`  `:(`  — emoticons in strings like `"Smileys :-)"` and
  `"happiest :)"` — keep them exactly as they appear.

**Weekdays and months:**

These are comma-separated lists. Keep exactly 7 weekdays (Monday first)
and exactly 12 months. Do not add spaces around commas.

```
msgid "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday"
msgstr "lundi,mardi,mercredi,jeudi,vendredi,samedi,dimanche"

msgid "January,February,...,December"
msgstr "janvier,février,...,décembre"
```

**Singular vs plural strings:**

Many stats have two versions — one for `{count} = 1` and one for `{count} > 1`.
Both must be translated, even if your language handles plurals differently.
The bot always picks the right one based on the count.

```
msgid "{nick} took voice away {count} time — someone had to."
msgstr "{nick} retirou voice {count} vez — alguém tinha de o fazer."

msgid "{nick} took voice away {count} times — someone had to."
msgstr "{nick} retirou voice {count} vezes — alguém tinha de o fazer."
```

**Style:**

- Keep the same tone — playful, slightly sarcastic, never mean
- Maintain gender-neutral language (`they/their` in English; use equivalent
  neutral forms or inclusive suffixes in your language)
- Keep the `!` and `?` punctuation where present — they contribute to the tone
- IRC terms (`op`, `halfop`, `voice`, `kick`) should not be translated —
  they are universally understood on IRC

---

---

## Português

### Como funciona

O Statsbot usa ficheiros PO padrão (`locale/<lang>.po`). Cada entrada tem este aspeto:

```
msgid "{nick} is a very aggressive person. They attacked others {count} times."
msgstr "{nick} é uma pessoa muito agressiva. Atacou outros {count} vezes."
```

O `msgid` é a frase em inglês — **não traduzas nem modifiques as linhas `msgid`**.
Edita apenas a linha `msgstr`. O ficheiro `en_US.po` tem `msgstr` vazio em todas
as entradas e serve como catálogo de referência; não lhe adiciones traduções.

### Regras

**Obrigatório manter — a página parte se forem removidos ou renomeados:**

- `{nick}` — nick IRC, inserido pelo bot
- `{nick2}` — segundo nick IRC (usado em frases de seguidor)
- `{count}` — um número
- `{pct}` — valor percentual (já formatado, ex: `27.3`)
- `{avg}` — um valor médio
- `{n}` — um número (dias, nicks, etc.)
- `{channel}` — nome do canal (ex: `#portugal`)
- `{network}` — nome da rede (ex: `PTirc`)
- `{url}` — um URL
- `{list}` — uma lista formatada
- `{date}` — uma data formatada
- `{time}` — uma hora formatada
- `{by}` — string de atribuição (pode estar vazio)
- `{quote}` — uma citação aleatória
- `{total}` — contagem total de nicks

Os marcadores podem ser **reordenados** dentro da frase para se adequarem à
gramática do teu idioma. Não os podes renomear, remover, nem adicionar novos.

**Não traduzir:**

- As quatro bandas horárias: `0-5`, `6-11`, `12-17`, `18-23` — deixar como estão.
- `Karma` — nome próprio, usado como título de secção e na base de dados.
- `Nick` (cabeçalho de coluna) — terminologia IRC universalmente compreendida.
- `URL`, `Smiley` — abreviaturas universais.
- `:-)`  `:)`  `:(`  — emoticons em frases como `"Smileys :-)"` — manter exatamente.

**Dias da semana e meses:**

Listas separadas por vírgulas. Manter exatamente 7 dias (começando na segunda-feira)
e exatamente 12 meses. Sem espaços à volta das vírgulas.

**Singular vs plural:**

Muitas estatísticas têm duas versões — uma para `{count} = 1` e outra para
`{count} > 1`. Ambas devem ser traduzidas.

**Estilo:**

- Manter o tom — bem-humorado, ligeiramente sarcástico, nunca cruel
- Usar linguagem neutra em termos de género (formas inclusivas como `-o/a` ou
  reestruturar a frase para evitar concordância de género)
- Manter os `!` e `?` onde presentes
- Termos IRC (`op`, `halfop`, `voice`, `kick`) não devem ser traduzidos

---

---

## Français

### Comment ça fonctionne

Statsbot utilise des fichiers PO standard (`locale/<lang>.po`). Chaque entrée ressemble à ceci :

```
msgid "{nick} is a very aggressive person. They attacked others {count} times."
msgstr "{nick} est une personne très agressive. A attaqué les autres {count} fois."
```

Le `msgid` est la phrase en anglais — **ne traduisez pas et ne modifiez pas les lignes `msgid`**.
Modifiez uniquement la ligne `msgstr`. Le fichier `en_US.po` a un `msgstr` vide pour toutes
les entrées et sert de catalogue de référence ; n'y ajoutez pas de traductions.

### Règles

**À conserver obligatoirement — la page se cassera si ces éléments sont supprimés ou renommés :**

- `{nick}` — pseudo IRC, inséré par le bot
- `{nick2}` — second pseudo IRC (utilisé dans les phrases de suiveur)
- `{count}` — un nombre
- `{pct}` — valeur en pourcentage (déjà formatée, ex : `27.3`)
- `{avg}` — une valeur moyenne
- `{n}` — un nombre (jours, pseudos, etc.)
- `{channel}` — nom du canal (ex : `#france`)
- `{network}` — nom du réseau (ex : `PTirc`)
- `{url}` — une URL
- `{list}` — une liste formatée
- `{date}` — une date formatée
- `{time}` — une heure formatée
- `{by}` — chaîne d'attribution (peut être vide)
- `{quote}` — une citation aléatoire
- `{total}` — nombre total de pseudos

Les marqueurs peuvent être **réordonnés** dans la phrase pour s'adapter à la
grammaire de votre langue. Vous ne pouvez pas les renommer, les supprimer,
ni en ajouter de nouveaux.

**À ne pas traduire :**

- Les quatre bandes horaires : `0-5`, `6-11`, `12-17`, `18-23` — laisser tels quels.
- `Karma` — nom propre, utilisé comme titre de section et dans la base de données.
- `Nick` (en-tête de colonne) — terminologie IRC universellement comprise.
- `URL`, `Smiley` — abréviations universelles.
- `:-)`  `:)`  `:(`  — émoticônes dans les chaînes comme `"Smileys :-)"` — à conserver tels quels.

**Jours de la semaine et mois :**

Listes séparées par des virgules. Conserver exactement 7 jours (en commençant par lundi)
et exactement 12 mois. Pas d'espaces autour des virgules.

**Singulier et pluriel :**

De nombreuses statistiques ont deux versions — une pour `{count} = 1` et une pour
`{count} > 1`. Les deux doivent être traduites.

**Style :**

- Conserver le ton — enjoué, légèrement sarcastique, jamais méchant
- Utiliser un langage neutre en termes de genre (formes inclusives avec `·e`,
  ou restructurer la phrase pour éviter l'accord de genre)
- Conserver les `!` et `?` là où ils sont présents
- Les termes IRC (`op`, `halfop`, `voice`, `kick`) ne doivent pas être traduits

---

---

## Italiano

### Come funziona

Statsbot utilizza file PO standard (`locale/<lang>.po`). Ogni voce ha questo aspetto:

```
msgid "{nick} is a very aggressive person. They attacked others {count} times."
msgstr "{nick} è una persona molto aggressiva. Ha attaccato gli altri {count} volte."
```

Il `msgid` è la frase in inglese — **non tradurre né modificare le righe `msgid`**.
Modifica solo la riga `msgstr`. Il file `en_US.po` ha `msgstr` vuoto per tutte
le voci e funge da catalogo di riferimento; non aggiungervi traduzioni.

### Regole

**Da conservare obbligatoriamente — la pagina si romperà se vengono rimossi o rinominati:**

- `{nick}` — nick IRC, inserito dal bot
- `{nick2}` — secondo nick IRC (usato nelle frasi del seguace)
- `{count}` — un numero
- `{pct}` — valore percentuale (già formattato, es: `27.3`)
- `{avg}` — un valore medio
- `{n}` — un numero (giorni, nick, ecc.)
- `{channel}` — nome del canale (es: `#italia`)
- `{network}` — nome della rete (es: `PTirc`)
- `{url}` — un URL
- `{list}` — un elenco formattato
- `{date}` — una data formattata
- `{time}` — un orario formattato
- `{by}` — stringa di attribuzione (può essere vuota)
- `{quote}` — una citazione casuale
- `{total}` — conteggio totale dei nick

I segnaposto possono essere **riordinati** nella frase per adattarsi alla
grammatica della tua lingua. Non puoi rinominarli, rimuoverli, né aggiungerne di nuovi.

**Da non tradurre:**

- Le quattro fasce orarie: `0-5`, `6-11`, `12-17`, `18-23` — lasciare invariate.
- `Karma` — nome proprio, usato come titolo di sezione e nel database.
- `Nick` (intestazione colonna) — terminologia IRC universalmente compresa.
- `URL`, `Smiley` — abbreviazioni universali.
- `:-)`  `:)`  `:(`  — emoticon nelle stringhe come `"Smileys :-)"` — conservare invariate.

**Giorni della settimana e mesi:**

Elenchi separati da virgole. Mantenere esattamente 7 giorni (a partire da lunedì)
ed esattamente 12 mesi. Nessuno spazio attorno alle virgole.

**Singolare e plurale:**

Molte statistiche hanno due versioni — una per `{count} = 1` e una per `{count} > 1`.
Entrambe devono essere tradotte.

**Stile:**

- Mantenere il tono — scherzoso, leggermente sarcastico, mai crudele
- Usare un linguaggio neutro rispetto al genere (forme inclusive come `-o/a`
  o ristrutturare la frase per evitare la concordanza di genere)
- Mantenere `!` e `?` dove presenti
- I termini IRC (`op`, `halfop`, `voice`, `kick`) non devono essere tradotti
