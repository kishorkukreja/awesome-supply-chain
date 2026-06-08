# Awesome Supply Chain

A comprehensive collection of research papers, implementations, libraries, and resources for supply chain management, organized by industry verticals.

132 AI Agent Skills Available!** This repository now includes a complete Claude Code plugin with supply chain skills for AI coding assistants. [See Skills Documentation →](./skills/)

## Table of Contents

- [Overview](#overview)
- [AI Skills Plugin](#ai-skills-plugin)
- [Industry Verticals](#industry-verticals)
- [General Research](#general-research)
- [Libraries & Tools](#libraries--tools)
- [Implementations](#implementations)
- [Contributing](#contributing)

## Overview

This repository consolidates the latest research, papers, implementations, and development libraries related to supply chain use cases across different industry verticals. It serves as a curated resource for researchers, practitioners, and developers working on supply chain optimization, management, and analytics.

## AI Skills Plugin

This repository includes **132 comprehensive AI agent skills** that work as a plugin for Claude Code and other AI coding assistants. These skills give your AI assistant expert knowledge in:

- **Supply Chain Planning** - Demand forecasting, S&OP, capacity planning
- **Operations Research** - VRP, TSP, facility location, knapsack, cutting stock
- **Inventory Management** - EOQ, safety stock, multi-echelon optimization
- **Warehouse Operations** - Slotting, routing, wave planning, automation
- **Transportation** - Route optimization, fleet management, last-mile delivery
- **Manufacturing** - Production scheduling, lean, quality management
- **Domain Expertise** - Retail, CPG, energy, healthcare, travel, manufacturing

### Quick Start

```bash
# Clone the repository
git clone https://github.com/kishorkukreja/awesome-supply-chain.git

# Install skills to Claude Code
cd awesome-supply-chain
cp -r skills/* ~/.claude/skills/
```

**Full Skills Documentation:** [skills/README.md](./skills/README.md)

**Plugin Configuration:** [.claude/README.md](./.claude/README.md)

## Industry Verticals

### [Consumer Packaged Goods (CPG)](./verticals/cpg/)
Supply chain research and implementations specific to consumer packaged goods industry, including network optimization, demand planning, and distribution strategies.

### [Retail](./verticals/retail/)
Retail supply chain management research covering omnichannel fulfillment, inventory optimization, last-mile delivery, and consumer-centric approaches.

### [Manufacturing](./verticals/manufacturing/)
Manufacturing supply chain topics including smart manufacturing, Industry 4.0, production planning, and digital twins.

### [Industrial](./verticals/industrial/)
Industrial supply chain research covering heavy equipment, B2B procurement, industrial IoT, and smart factories.

### [Energy](./verticals/energy/)
Energy supply chain optimization including renewable energy, oil & gas logistics, energy storage systems, and sustainable energy networks.

### [Transportation & Logistics](./verticals/transportation/)
Transportation and logistics optimization covering route planning, fleet management, warehouse operations, and last-mile delivery.

### [Travel & Hospitality](./verticals/travel/)
Tourism and hospitality supply chain management including hotel operations, tour operators, and service chain optimization.

### [Cross-Industry](./verticals/cross-industry/)
Research and solutions applicable across multiple industries, including general supply chain principles and emerging technologies.

## General Research

### [Core Topics](./general-research/)

- **Machine Learning & AI**: Deep learning, reinforcement learning, and predictive analytics for supply chain
- **Optimization**: Mathematical optimization, linear programming, metaheuristics
- **Digital Technologies**: Blockchain, IoT, Digital Twins, Cloud Computing
- **Sustainability**: Green supply chain, circular economy, carbon footprint reduction
- **Risk Management**: Supply chain resilience, disruption management, scenario planning
- **Demand Forecasting**: Time series analysis, forecasting models, demand sensing
- **Inventory Management**: Stock optimization, safety stock, multi-echelon inventory
- **Network Design**: Facility location, distribution network optimization

## Libraries & Tools

### [Python Libraries](./libraries/)

Popular Python libraries for supply chain analysis and optimization:
- **supplychainpy**: Supply chain analysis, modeling and simulation
- **PuLP**: Linear programming
- **OR-Tools**: Google's optimization tools
- **NetworkX**: Network analysis and optimization
- **Prophet**: Time series forecasting
- **TensorFlow/PyTorch**: Deep learning frameworks

### Data & Analytics Tools
- **Tableau**: Supply chain visualization
- **Power BI**: Business intelligence and analytics
- **Apache Spark**: Big data processing
- **Apache Kafka**: Real-time data streaming
- [TWZRD Agent Intel](https://intel.twzrd.xyz) - Trust scoring for AI agents on Solana. Verify agent wallet identity before x402 micropayments. Free MCP: `{"mcpServers":{"twzrd-agent-intel":{"url":"https://intel.twzrd.xyz/mcp"}}}`

## Implementations

### [GitHub Repositories](./implementations/)

Curated list of open-source implementations:
- Supply chain optimization algorithms
- Demand forecasting models
- Inventory management systems
- Route optimization solutions
- Warehouse management systems
- Supply chain simulation frameworks

## Key Research Themes (2024-2025)

### Emerging Technologies
- **Generative AI**: ChatGPT and LLMs for supply chain planning
- **Digital Twins**: Virtual replicas for simulation and optimization
- **Autonomous Systems**: Self-driving vehicles, drones, robots
- **5G & Edge Computing**: Real-time decision making at the edge

### Sustainability Focus
- Carbon footprint reduction and tracking
- Circular economy and closed-loop supply chains
- Sustainable sourcing and ethical procurement
- Green logistics and eco-friendly transportation

### Resilience & Risk
- Supply chain resilience frameworks
- Multi-sourcing and supplier diversification
- Scenario planning and risk mitigation
- Real-time visibility and control towers

## Contributing

We welcome contributions! Please follow these guidelines:

1. **Adding Papers**: Include title, authors, publication venue, year, and DOI/link
2. **Adding Implementations**: Provide repository link, description, and key features
3. **Adding Libraries**: Include installation instructions, use cases, and examples
4. **Adding/Improving Skills**: Contribute to the AI skills in the `skills/` directory
5. **Quality Standards**: Ensure resources are from reputable sources and are recent (preferably 2020+)

### Contributing Skills

The `skills/` directory contains 132 AI agent skills for supply chain problems. To contribute:

1. Follow the existing skill structure (YAML frontmatter + markdown content)
2. Include working Python code examples
3. Add relevant algorithms and frameworks
4. Reference industry best practices
5. See [skills/README.md](./skills/README.md) for detailed guidelines

## Research Sources

- Academic journals (Scopus, Web of Science, Google Scholar)
- Conference proceedings (ACM, IEEE, INFORMS)
- Industry reports (Gartner, McKinsey, Forrester)
- Open-source repositories (GitHub, GitLab)
- Preprint servers (arXiv, SSRN)

## License

This repository is maintained for educational and research purposes. Please respect the licenses of individual papers, libraries, and implementations.

## Maintainers

This repository is actively maintained and updated with the latest research and developments in supply chain management.

---

**Last Updated**: October 2025
