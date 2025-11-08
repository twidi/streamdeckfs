# Flux de Données - Architecture Technique

## Vue d'ensemble

StreamDeckFS utilise une architecture **event-driven** où le système de fichiers sert de source de vérité pour la configuration. Le flux de données est structuré en plusieurs phases : détection des changements, parsing, création d'entités, résolution de variables, et rendu.

## 1. Cycle de vie des données

```
Fichier modifié
    ↓ (inotify < 500ms)
FilesWatcher
    ↓
WatchedDirectory.on_file_change()
    ↓
Entity.parse_filename() → ParseFilenameResult
    ↓
Entity.create_from_args() → Instance Entity
    ↓
VersionProxy.add_version() → version_activated()
    ↓
Résolution variables → Cascade (Key → Page → Deck → Référence → Env)
    ↓
Key.render() → compose_image() (PIL)
    ↓
RenderThread (queue timestampée)
    ↓
deck.set_key_image() (délai min 10ms)
    ↓
Affichage StreamDeck
```

**Latence totale** : 50-200ms (inotify + parse + render + queue)

## 2. Surveillance du système de fichiers

### 2.1. FilesWatcher (streamdeckfs/watchers/)

Thread dédié qui surveille les modifications via inotify (Linux).

**Fichiers clés** :
- `watchers/inotify.py` : Implémentation inotify
- `watchers/base.py` : Interface abstraite

**Événements surveillés** :
- `CREATE` : Nouveau fichier
- `MODIFY` : Modification de contenu
- `DELETE` : Suppression
- `MOVED_FROM` / `MOVED_TO` : Renommage

### 2.2. WatchedDirectory Tree

Hiérarchie de watchers correspondant à la structure Deck → Page → Key.

```python
# streamdeckfs/entities/base.py
class EntityDir(Entity):
    def on_file_change(self, path, flags, directory):
        # Propage le changement aux entités concernées
```

**Propagation** : Parent → Enfant → Versions

## 3. Parsing et création d'entités

### 3.1. Format des noms de fichiers

```
ENTITY_TYPE_MAIN_PART;arg1=value1;arg2=value2;...
```

**Exemples** :
- `PAGE_1` : Page numéro 1
- `KEY_1,1;disabled=true` : Touche position (1,1) désactivée
- `VAR_COUNTER;value=0` : Variable COUNTER initialisée à 0
- `ON_PRESS;command=echo "test"` : Événement au clic

### 3.2. Entity.parse_filename()

**Fichier** : `streamdeckfs/entities/base.py:300-450`

```python
@classmethod
def parse_filename(cls, path, vars_getter=None, env_vars=None):
    # 1. Extraction du nom de fichier
    # 2. Substitution des variables $VAR_NAME
    # 3. Évaluation des expressions {1+2}
    # 4. Parsing via regex (main_part_re)
    # 5. Parsing des arguments (allowed_args)
    # 6. Retourne ParseFilenameResult
```

**Résultat** :
```python
ParseFilenameResult(
    ref_conf=None,  # Configuration de référence
    ref=None,       # Référence à une autre entité
    main=(...),     # Partie principale parsée (tuple)
    args={...},     # Arguments parsés (dict)
    used_vars={},   # Variables utilisées
    used_env_vars=set()  # Variables d'env utilisées
)
```

**Cache** : Les résultats sont mis en cache (`parse_cache`) pour optimiser les re-parsings.

### 3.3. Entity.create_from_args()

**Fichier** : `streamdeckfs/entities/base.py:500-600`

Crée l'instance d'entité à partir du résultat du parsing.

```python
@classmethod
def create_from_args(cls, path, parse_result, parent, **kwargs):
    # Convertit parse_result en arguments pour __init__
    # Appelle le constructeur de la classe d'entité
    return cls(path=path, parent=parent, **entity_args)
```

## 4. Système de versions (VersionProxy)

### 4.1. Principe

Chaque entité peut avoir plusieurs versions identifiées par leur chemin (path). Le `VersionProxy` maintient la version active et permet le switch dynamique.

**Fichier** : `streamdeckfs/entities/base.py:800-900`

```python
class VersionProxy(ObjectWrapper):
    def add_version(self, new_version):
        # Ajoute une nouvelle version
        # Active automatiquement si première ou prioritaire
        self.version_activated(new_version)

    def remove_version(self, version):
        # Supprime une version
        # Active la suivante si c'était l'active
```

### 4.2. Activation de version

Lors de l'activation (`version_activated()`), cascade de mises à jour :

1. **Entité** : Met à jour ses propriétés
2. **Variables** : Recalcule les valeurs
3. **Enfants** : Notifie les enfants (Pages, Keys)
4. **Rendu** : Déclenche le re-rendu si nécessaire

## 5. Gestion des variables

### 5.1. Types de variables

**Fichier** : `streamdeckfs/entities/var.py`

| Type | Format | Exemple |
|------|--------|---------|
| Simple | `VAR_NAME;value=X` | `VAR_COUNTER;value=0` |
| Fichier | `VAR_NAME;file=path` | `VAR_STATUS;file=status.txt` |
| Conditionnelle | `VAR_NAME;if=cond;then=X;else=Y` | `VAR_COLOR;if=$VAR_ON;then=green;else=red` |
| Expression | `VAR_NAME;value={expr}` | `VAR_TOTAL;value={$VAR_A+$VAR_B}` |

### 5.2. Cascade de résolution

Lors de la substitution d'une variable `$VAR_NAME`, recherche dans l'ordre :

1. **Variables de la Key** : `key.vars['NAME']`
2. **Variables de la Page** : `page.vars['NAME']`
3. **Variables du Deck** : `deck.vars['NAME']`
4. **Référence** : `entity.reference.vars['NAME']` (si référence)
5. **Variables d'environnement** : `os.environ['NAME']`

**Fichier** : `streamdeckfs/entities/base.py:200-250`

```python
def get_vars(self, name=None, allow_disabled=True):
    # Retourne le dictionnaire de variables approprié
    # en suivant la cascade
```

### 5.3. Dépendances et topological sort

**Fichier** : `streamdeckfs/entities/var.py:150-200`

Les variables peuvent dépendre d'autres variables. Pour éviter les cycles et garantir l'ordre de calcul :

```python
import networkx as nx

def get_vars_in_order(self):
    # Construit un graphe de dépendances
    graph = nx.DiGraph()
    # Ajoute les nœuds et arcs
    # Retourne topological_sort(graph)
```

### 5.4. Gestion de l'indisponibilité

**Exception** : `UnavailableVar`

Levée quand une variable n'est pas encore disponible. L'entité passe en état "waiting" et sera réactivée quand la variable devient disponible.

```python
try:
    result = entity.parse_filename(path, vars_getter)
except UnavailableVar:
    entity.waiting = True
    # Sera réactivé lors de la mise à jour de la variable
```

## 6. Rendu des images

### 6.1. Pipeline de composition

**Fichier** : `streamdeckfs/entities/key.py:400-600`

```python
def compose_image(self):
    # 1. Crée une image vierge (PIL.Image)
    base_image = Image.new('RGB', (key_width, key_height))

    # 2. Compose les couches (ImageLayer)
    for layer in self.images.values():
        layer_image = layer.get_image()
        base_image.paste(layer_image, position, mask)

    # 3. Ajoute les textes (TextLine)
    draw = ImageDraw.Draw(base_image)
    for text in self.texts.values():
        draw.text(position, text.value, font, fill)

    # 4. Cache le résultat
    self.composed_image = base_image
    return base_image
```

### 6.2. RenderThread et queue

**Fichier** : `streamdeckfs/common.py:400-500`

Chaque Deck a son propre thread de rendu avec une queue timestampée.

```python
class RenderThread(Thread):
    def run(self):
        while self.is_running:
            timestamp, key_index, image = self.queue.get()

            # Attend minimum RENDER_IMAGE_DELAY (10ms)
            elapsed = time() - timestamp
            if elapsed < RENDER_IMAGE_DELAY:
                sleep(RENDER_IMAGE_DELAY - elapsed)

            # Affiche l'image
            deck.device.set_key_image(key_index, image)
```

**Raison** : Évite de saturer le StreamDeck avec trop de mises à jour simultanées.

### 6.3. Cache multi-niveaux

1. **Parse cache** : Résultats de `parse_filename()`
2. **Expression cache** : Résultats des expressions mathématiques
3. **Image composition cache** : Images composées dans `Key.composed_image`
4. **Text rendering cache** : Textes rendus dans `TextLine`

## 7. Événements système et métier

### 7.1. Événements métier

**Fichier** : `streamdeckfs/entities/event.py`

| Événement | Déclencheur | Exemple |
|-----------|-------------|---------|
| `ON_START` | Démarrage du deck | Initialisation |
| `ON_PRESS` | Touche pressée | Lancement commande |
| `ON_RELEASE` | Touche relâchée | Arrêt action |
| `ON_LONGPRESS` | Appui long (> 500ms) | Menu contextuel |
| `ON_END` | Fermeture du deck | Nettoyage |

### 7.2. Exécution asynchrone

**Fichier** : `streamdeckfs/common.py:600-700`

```python
class Manager:
    def start_process(self, command, env=None, cwd=None):
        # Lance le processus en arrière-plan
        process = Popen(
            command,
            shell=True,
            env=env,
            cwd=cwd,
            stdout=DEVNULL,
            stderr=DEVNULL
        )
        # Surveille le processus dans un thread séparé
```

**Non-bloquant** : Les commandes sont exécutées sans bloquer le thread principal.

## 8. Architecture multi-thread

### 8.1. Threads actifs

| Thread | Rôle | Fichier |
|--------|------|---------|
| **Main** | Gestion des decks, événements clavier | `__main__.py` |
| **FilesWatcher** | Surveillance inotify | `watchers/inotify.py` |
| **RenderThread** (per Deck) | Rendu des images avec throttling | `common.py` |
| **ProcessWatcher** | Surveillance des processus enfants | `common.py` |
| **WebServer** (optionnel) | API HTTP | `web.py` |

### 8.2. Synchronisation

**Thread-local storage** : `thread_local` pour contexte de parsing

```python
# streamdeckfs/entities/base.py
from threading import local
thread_local = local()
```

**Queues thread-safe** : `SimpleQueue` pour communication entre threads

## 9. Cas d'usage : Modification d'une variable

Scénario : L'utilisateur modifie `VAR_COUNTER;value=5`

```
1. inotify détecte le changement (< 500ms)
2. FilesWatcher.on_file_change() appelé
3. WatchedDirectory propage au parent (Deck/Page)
4. Entity.parse_filename('VAR_COUNTER;value=5')
   → ParseFilenameResult(main=('COUNTER',), args={'value': '5'})
5. Var.create_from_args() crée l'instance
6. VersionProxy.add_version(new_var)
7. Version activée → var.version_activated()
8. Notification à tous les used_by (entités utilisant cette var)
9. Chaque entité re-parse son nom avec la nouvelle valeur
10. Si Keys impactées → Key.render() → compose_image()
11. Image ajoutée à RenderThread.queue
12. RenderThread affiche après délai minimum (10ms)
```

**Durée totale** : ~50-150ms

## 10. Cas d'usage : Appui sur une touche

Scénario : L'utilisateur appuie sur la touche (1, 2)

```
1. StreamDeck.key_callback(key_index=1, pressed=True)
2. Deck.key_change_callback(deck, key_index, pressed)
3. Conversion index → (row, col) : (1, 2)
4. Recherche de la Key dans current_page
5. Deck.pressed_key = key (mémorisation)
6. Recherche ON_PRESS dans key.events
7. Si trouvé : Manager.start_process(event.command, env)
8. Timer lancé pour détecter LONGPRESS (> 500ms)
9. --- Utilisateur relâche ---
10. key_callback(pressed=False)
11. Si timer < 500ms : Annule LONGPRESS, trigger ON_RELEASE
12. Si timer >= 500ms : ON_LONGPRESS déjà déclenché
13. Deck.pressed_key = None
```

**Gestion des états** : Touche enfoncée mémorisée dans `Deck.pressed_key`

## 11. Optimisations et performances

### 11.1. Caching

- **Parse cache** : Évite re-parsing des noms de fichiers identiques
- **Expression cache** : Évite re-calcul des expressions mathématiques
- **Image cache** : Évite recomposition si aucun changement

### 11.2. Throttling

- **RENDER_IMAGE_DELAY** : 10ms minimum entre affichages (common.py:74)
- **Queue timestampée** : Garantit l'ordre et le délai

### 11.3. Lazy evaluation

- Variables résolues uniquement quand nécessaires
- Images composées uniquement lors du rendu

### 11.4. Topological sort

- Calcul optimal de l'ordre des variables dépendantes
- Évite calculs redondants

## 12. Points d'extension

### 12.1. Nouveaux types d'entités

Hériter de `Entity` ou `EntityDir` et définir :
- `path_glob` : Pattern de fichier
- `main_part_re` : Regex de parsing
- `allowed_args` : Arguments autorisés
- `create_from_args()` : Logique de création

### 12.2. Nouveaux types de variables

Hériter de `Var` et implémenter `get_value()` avec logique personnalisée.

### 12.3. Nouveaux watchers

Implémenter `BaseWatcher` pour d'autres systèmes (macOS FSEvents, Windows ReadDirectoryChangesW).

## Références

- **Fichiers principaux** :
  - `streamdeckfs/common.py` (Manager, RenderThread, 735 lignes)
  - `streamdeckfs/entities/base.py` (Entity, VersionProxy, parsing, 1200+ lignes)
  - `streamdeckfs/entities/key.py` (Key, rendu, composition, 600+ lignes)
  - `streamdeckfs/entities/var.py` (Variables, dépendances, 400+ lignes)
  - `streamdeckfs/watchers/inotify.py` (Surveillance fichiers, 200+ lignes)

- **Concepts clés** :
  - Event-driven architecture
  - Filesystem as database
  - Multi-threading with queues
  - Lazy evaluation + caching
  - Topological sorting for dependencies
  - Version management with proxies

---

**Note** : Cette documentation se concentre sur le flux de données. Pour l'architecture globale, voir les autres documents techniques.
