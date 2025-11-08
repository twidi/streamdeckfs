# StreamDeckFS Technical Documentation

Complete technical documentation for the StreamDeckFS project.

## Documentation Structure

This documentation is organized into two comprehensive guides:

### 1. [System Architecture](./system-architecture.md)

**Core system design and implementation details**

Covers the fundamental architecture, design patterns, and internal workings of StreamDeckFS:

- System overview and architecture diagrams
- Core components (Manager, Deck, Page, Key entities)
- Configuration model and filesystem structure
- Image rendering pipeline
- Event handling system
- Variable system with dependency graphs
- Multi-threading architecture
- Web virtual decks
- File watching and hot reload
- Design patterns used throughout the codebase

**Target Audience:** Developers wanting to understand the codebase, contributors, and advanced users exploring internal mechanisms.

**Length:** ~1,200 lines with Mermaid diagrams

---

### 2. [Advanced Features Guide](./advanced-features.md)

**Comprehensive guide to advanced features and usage patterns**

Covers all advanced features, APIs, and real-world usage scenarios:

#### Core Features
- **REST API System** - Complete CLI API for programmatic configuration management
- **Drawing System** - Vector graphics with primitives (lines, rectangles, circles, pie charts)
- **Advanced Variables** - Expression evaluation, conditionals, file-based variables, dependency graphs
- **Advanced Configuration** - Naming, enable/disable, duration constraints, quiet mode, text fitting

#### State & Control
- **State Management Files** - Control files (.model, .current_page, .brightness)
- **Advanced Event System** - ON_START, ON_END, ON_DELAY, ON_REPEAT, event chaining, references
- **Advanced Image Composition** - Multi-layer composition, backgrounds, opacity, transformations

#### Practical Application
- **Complete Use Cases** - Real-world examples:
  - Pomodoro Timer (state machine with 4 states, visual progress)
  - Stopwatch (start/stop/reset functionality)
  - Volume Control (VU meter, fader visualization)
  - Media Player Control (now playing, dynamic icons)
  - System Monitor (CPU/RAM/temperature gauges)

#### Operations & Deployment
- **Hardware Compatibility** - Supported Stream Deck models (Original, V2, Mini, XL), resolution independence, display transformations
- **Troubleshooting & Debugging** - Common issues, debugging tools, log levels
- **Performance Optimization** - Image rendering, variables, events, memory, disk I/O, network
- **Security Considerations** - Command injection prevention, file permissions, expression sandboxing, web server security

**Target Audience:** Power users, system administrators, automation engineers, and anyone building complex Stream Deck configurations.

**Length:** ~2,000 lines with extensive code examples

---

## Quick Navigation

### For New Users
1. Start with [System Architecture - Overview](./system-architecture.md#overview)
2. Learn about [Configuration Model](./system-architecture.md#configuration-model)
3. Explore [Complete Use Cases](./advanced-features.md#complete-use-cases)

### For Developers
1. Review [Architecture](./system-architecture.md#architecture)
2. Study [Core Components](./system-architecture.md#core-components)
3. Understand [Multi-Threading](./system-architecture.md#multi-threading-architecture)
4. Explore [Design Patterns](./system-architecture.md#design-patterns)

### For Power Users
1. Master [REST API System](./advanced-features.md#rest-api-system)
2. Learn [Drawing System](./advanced-features.md#drawing-system)
3. Explore [Advanced Variables](./advanced-features.md#advanced-variable-features)
4. Study [Complete Use Cases](./advanced-features.md#complete-use-cases)

### For DevOps/SysAdmins
1. Review [Hardware Compatibility](./advanced-features.md#hardware-compatibility)
2. Study [Troubleshooting](./advanced-features.md#troubleshooting--debugging)
3. Implement [Performance Optimization](./advanced-features.md#performance-optimization)
4. Apply [Security Considerations](./advanced-features.md#security-considerations)

---

## Documentation Coverage Map

| Topic | System Architecture | Advanced Features |
|-------|-------------------|------------------|
| **Core Concepts** |
| Project overview | ✓ Overview | ✓ Use Cases |
| Architecture diagrams | ✓ Architecture | - |
| Core components | ✓ Core Components | - |
| Configuration basics | ✓ Configuration Model | - |
| **Image System** |
| Rendering pipeline | ✓ Image Rendering Pipeline | - |
| Drawing system | - | ✓ Drawing System |
| Advanced composition | - | ✓ Advanced Image Composition |
| **Event System** |
| Basic event handling | ✓ Event Handling System | - |
| Advanced events | - | ✓ Advanced Event System |
| **Variable System** |
| Basic variables | ✓ Variable System | - |
| Advanced variables | - | ✓ Advanced Variable Features |
| **Infrastructure** |
| Multi-threading | ✓ Multi-Threading Architecture | - |
| File watching | ✓ File Watching & Hot Reload | - |
| Web virtual decks | ✓ Web Virtual Decks | - |
| **Advanced Topics** |
| REST API | - | ✓ REST API System |
| Configuration parameters | - | ✓ Advanced Configuration Parameters |
| State management | - | ✓ State Management Files |
| **Real-World Usage** |
| Use case examples | - | ✓ Complete Use Cases |
| Hardware compatibility | - | ✓ Hardware Compatibility |
| Troubleshooting | - | ✓ Troubleshooting & Debugging |
| Performance tuning | - | ✓ Performance Optimization |
| Security | - | ✓ Security Considerations |
| **Development** |
| Design patterns | ✓ Design Patterns | - |
| Code organization | ✓ (throughout) | - |

---

## Examples Directory

Practical examples demonstrating advanced features:

- **Pomodoro Timer** (`../../examples/pomodoro/`) - Full-featured productivity timer
- **Stopwatch** (`../../examples/stopwatch/`) - Simple timer with state machine

---

## Contributing to Documentation

When adding or updating documentation:

1. **System Architecture** - Internal implementation details, architecture decisions, design patterns
2. **Advanced Features** - User-facing features, usage patterns, examples, troubleshooting

### Documentation Standards

- Use **Mermaid diagrams** for visualizations
- Include **code examples** for clarity
- Provide **real-world use cases** when applicable
- Keep **tables** for parameter references
- Add **cross-references** between documents
- Use **consistent heading levels**

### Example Code Format

```
# Filename or description
FILE_OR_ENTITY_NAME/
├── COMPONENT;param=value
└── ANOTHER_COMPONENT

# Inline examples
VAR_EXAMPLE;value={$VAR_A + $VAR_B}
```

---

## Additional Resources

- **Main Repository:** [github.com/twidi/streamdeckfs](https://github.com/twidi/streamdeckfs)
- **Examples:** [../../examples/](../../examples/)
- **README:** [../../README.md](../../README.md)

---

## Documentation Statistics

| Document | Lines | Topics | Diagrams | Examples |
|----------|-------|--------|----------|----------|
| System Architecture | ~1,200 | 11 | 6+ Mermaid | Code snippets |
| Advanced Features | ~2,000 | 12 | 1 Mermaid | 5+ complete use cases |
| **Total** | **~3,200** | **23** | **7+** | **50+** |

---

## License

This documentation is part of StreamDeckFS.

Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>

License: MIT, see https://opensource.org/licenses/MIT
