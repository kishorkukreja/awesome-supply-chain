# Supply Chain Implementations

Curated collection of open-source implementations, GitHub repositories, and practical examples for supply chain management.

## Table of Contents

- [General Supply Chain](#general-supply-chain)
- [Demand Forecasting](#demand-forecasting)
- [Inventory Management](#inventory-management)
- [Route Optimization](#route-optimization)
- [Warehouse Management](#warehouse-management)
- [Network Design](#network-design)
- [Simulation](#simulation)
- [Data Science Projects](#data-science-projects)
- [Full Systems](#full-systems)

## General Supply Chain

### supply-chain-optimization (samirsaci)

**Description**: Comprehensive supply chain optimization examples in Python
- **GitHub**: https://github.com/samirsaci/supply-chain-optimization
- **Topics**:
  - Inventory optimization
  - Route optimization
  - Demand forecasting
  - Network design
  - Distribution planning
- **Technologies**: Python, PuLP, pandas, matplotlib
- **Blog**: https://samirsaci.com

### supplychainpy

**Description**: Python library for supply chain analysis with example implementations
- **GitHub**: https://github.com/KevinFasusi/supplychainpy
- **Features**:
  - Inventory analysis
  - ABC/XYZ classification
  - Economic order quantity
  - Reorder point calculations
  - Safety stock optimization
- **Documentation**: Comprehensive tutorials and examples

### Awesome China Sourcing (assassinationss)

**Description**: Open-source supplier verification, negotiation, and payment-safety toolkit for importing from China
- **GitHub**: https://github.com/assassinationss/awesome-china-sourcing
- **Topics**:
  - Supplier verification (six-step VERIFY system)
  - Landed cost calculation
  - CNY / Golden Week order deadline planning
  - Negotiation and quality-inspection templates
- **Technologies**: Markdown guides, client-side JavaScript tools

## Demand Forecasting

### Time Series Forecasting Examples

**Repository**: Multiple implementations of forecasting models
- **Topics**:
  - ARIMA and SARIMA models
  - Prophet implementation
  - LSTM and GRU networks
  - Ensemble forecasting
  - Feature engineering
  - Cross-validation strategies

### Demand Forecasting with ML

**Common Implementations**:
1. **Prophet for Retail Demand**
   - Seasonal patterns
   - Holiday effects
   - Promotional impacts

2. **LSTM for Sequential Data**
   - Multi-step ahead forecasting
   - Multivariate inputs
   - Attention mechanisms

3. **XGBoost for Demand**
   - Feature engineering
   - Lag features
   - External variables

4. **Ensemble Methods**
   - Model averaging
   - Stacking
   - Weighted combinations

### Example Projects

**Walmart Sales Forecasting** (Kaggle)
- Historical sales data
- Store and department level
- Holiday effects
- Multiple forecasting approaches

**Rossmann Store Sales** (Kaggle)
- Daily sales forecasting
- Competition effects
- Promotional impacts
- Deep learning approaches

## Inventory Management

### Multi-Echelon Inventory Optimization

**Implementation Topics**:
- Safety stock placement
- Service level optimization
- Inventory pooling
- Risk pooling strategies
- Dynamic safety stock

**Example Code**:
```python
# Safety stock calculation
from scipy import stats

def calculate_safety_stock(demand_std, lead_time, service_level):
    """
    Calculate safety stock using normal distribution

    Args:
        demand_std: Standard deviation of demand
        lead_time: Lead time in days
        service_level: Desired service level (e.g., 0.95)

    Returns:
        Safety stock quantity
    """
    z_score = stats.norm.ppf(service_level)
    safety_stock = z_score * demand_std * (lead_time ** 0.5)
    return safety_stock
```

### ABC/XYZ Analysis

**Implementation**:
```python
import pandas as pd
import numpy as np

def abc_analysis(df, value_col, item_col):
    """
    Perform ABC analysis on inventory items

    Args:
        df: DataFrame with items and values
        value_col: Column name for values
        item_col: Column name for items

    Returns:
        DataFrame with ABC classification
    """
    # Sort by value descending
    df_sorted = df.sort_values(value_col, ascending=False).copy()

    # Calculate cumulative percentage
    df_sorted['cum_value'] = df_sorted[value_col].cumsum()
    total_value = df_sorted[value_col].sum()
    df_sorted['cum_pct'] = df_sorted['cum_value'] / total_value * 100

    # Classify
    df_sorted['class'] = 'C'
    df_sorted.loc[df_sorted['cum_pct'] <= 80, 'class'] = 'A'
    df_sorted.loc[(df_sorted['cum_pct'] > 80) & (df_sorted['cum_pct'] <= 95), 'class'] = 'B'

    return df_sorted
```

### Economic Order Quantity (EOQ)

**Implementation**:
```python
import numpy as np

def calculate_eoq(annual_demand, order_cost, holding_cost):
    """
    Calculate Economic Order Quantity

    Args:
        annual_demand: Annual demand quantity
        order_cost: Cost per order
        holding_cost: Annual holding cost per unit

    Returns:
        Optimal order quantity
    """
    eoq = np.sqrt((2 * annual_demand * order_cost) / holding_cost)
    return eoq

def calculate_total_cost(demand, order_qty, order_cost, holding_cost):
    """Calculate total inventory cost"""
    num_orders = demand / order_qty
    ordering_cost = num_orders * order_cost
    avg_inventory = order_qty / 2
    holding_cost_total = avg_inventory * holding_cost
    return ordering_cost + holding_cost_total
```

## Route Optimization

### Vehicle Routing Problem (VRP) Implementations

#### Google OR-Tools VRP Examples

**GitHub**: https://github.com/google/or-tools
**Examples**:
- Basic VRP
- VRP with time windows
- VRP with capacity constraints
- Multi-depot VRP
- Pickup and delivery

**Example**:
```python
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

def solve_vrp(distance_matrix, num_vehicles, depot):
    """
    Solve Vehicle Routing Problem

    Args:
        distance_matrix: 2D array of distances
        num_vehicles: Number of vehicles
        depot: Depot location index

    Returns:
        Solution object
    """
    # Create routing index manager
    manager = pywrapcp.RoutingIndexManager(
        len(distance_matrix), num_vehicles, depot
    )

    # Create routing model
    routing = pywrapcp.RoutingModel(manager)

    # Define cost callback
    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Set search parameters
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )

    # Solve
    solution = routing.SolveWithParameters(search_parameters)
    return solution, routing, manager
```

#### VROOM

**Description**: Open-source optimization engine for VRP
- **GitHub**: https://github.com/VROOM-Project/vroom
- **Features**:
  - Fast solving
  - Time windows
  - Multiple vehicles
  - Skills and restrictions
  - JSON API

#### jsprit

**Description**: Java-based VRP solver
- **GitHub**: https://github.com/graphhopper/jsprit
- **Features**:
  - Various VRP variants
  - Flexible constraints
  - Rich API

### Traveling Salesman Problem (TSP)

**Implementations**:
- Exact algorithms (Dynamic Programming)
- Heuristics (Nearest Neighbor, 2-opt)
- Metaheuristics (Genetic Algorithm, Simulated Annealing)

```python
import numpy as np
from itertools import permutations

def tsp_brute_force(distance_matrix):
    """
    Solve TSP using brute force (small instances only)

    Args:
        distance_matrix: 2D array of distances

    Returns:
        Best route and distance
    """
    n = len(distance_matrix)
    cities = list(range(1, n))  # Exclude depot

    best_distance = float('inf')
    best_route = None

    # Try all permutations
    for perm in permutations(cities):
        route = [0] + list(perm) + [0]
        distance = sum(
            distance_matrix[route[i]][route[i+1]]
            for i in range(len(route)-1)
        )

        if distance < best_distance:
            best_distance = distance
            best_route = route

    return best_route, best_distance
```

## Warehouse Management

### Warehouse Layout Optimization

**Topics**:
- Slotting optimization
- Pick path optimization
- Storage location assignment
- Cross-docking design

### Order Batching

**Implementation**:
```python
import numpy as np
from sklearn.cluster import KMeans

def order_batching_clustering(orders, n_batches):
    """
    Batch orders using clustering

    Args:
        orders: Array of order locations
        n_batches: Number of batches

    Returns:
        Batch assignments
    """
    kmeans = KMeans(n_clusters=n_batches, random_state=42)
    batch_assignments = kmeans.fit_predict(orders)
    return batch_assignments
```

### Picker Routing

**Strategies**:
- S-shape routing
- Largest gap
- Combined (composite)
- Optimal (Dynamic Programming)

## Network Design

### Facility Location Problem

**Implementation using PuLP**:
```python
from pulp import *

def facility_location(demands, capacities, fixed_costs, transport_costs):
    """
    Solve facility location problem

    Args:
        demands: Customer demands
        capacities: Facility capacities
        fixed_costs: Fixed costs to open facilities
        transport_costs: Transport cost matrix

    Returns:
        Optimal solution
    """
    # Define problem
    prob = LpProblem("Facility_Location", LpMinimize)

    # Indices
    customers = range(len(demands))
    facilities = range(len(capacities))

    # Decision variables
    y = LpVariable.dicts("facility", facilities, cat='Binary')
    x = LpVariable.dicts("flow",
                         [(i,j) for i in customers for j in facilities],
                         lowBound=0)

    # Objective: minimize total cost
    prob += (
        lpSum([fixed_costs[j] * y[j] for j in facilities]) +
        lpSum([transport_costs[i][j] * x[i,j]
               for i in customers for j in facilities])
    )

    # Constraints
    # Demand satisfaction
    for i in customers:
        prob += lpSum([x[i,j] for j in facilities]) == demands[i]

    # Capacity constraints
    for j in facilities:
        prob += lpSum([x[i,j] for i in customers]) <= capacities[j] * y[j]

    # Solve
    prob.solve()

    return prob, x, y
```

### Supply Chain Network Optimization

**Topics**:
- Multi-echelon network design
- Hub-and-spoke vs. direct shipment
- Network flow optimization
- Capacity planning

## Simulation

### SimPy Warehouse Simulation

**Example**:
```python
import simpy
import random

class Warehouse:
    def __init__(self, env, num_workers):
        self.env = env
        self.workers = simpy.Resource(env, num_workers)
        self.orders_processed = 0

    def process_order(self, order_id, processing_time):
        """Process an order"""
        print(f'{order_id} arrives at {self.env.now:.2f}')
        with self.workers.request() as request:
            yield request
            print(f'{order_id} starts processing at {self.env.now:.2f}')
            yield self.env.timeout(processing_time)
            self.orders_processed += 1
            print(f'{order_id} completed at {self.env.now:.2f}')

def order_generator(env, warehouse):
    """Generate orders"""
    order_num = 0
    while True:
        yield env.timeout(random.expovariate(1.0/5.0))  # Avg 5 minutes
        order_num += 1
        processing_time = random.uniform(2, 8)
        env.process(warehouse.process_order(f'Order {order_num}', processing_time))

# Run simulation
env = simpy.Environment()
warehouse = Warehouse(env, num_workers=3)
env.process(order_generator(env, warehouse))
env.run(until=100)
print(f'Total orders processed: {warehouse.orders_processed}')
```

### Supply Chain Simulation

**Topics**:
- Bullwhip effect demonstration
- Inventory dynamics
- Production-inventory systems
- Multi-echelon systems

## Data Science Projects

### Kaggle Competition Solutions

**Supply Chain Competitions**:
- Walmart Recruiting - Store Sales Forecasting
- Rossmann Store Sales
- Corporación Favorita Grocery Sales Forecasting
- M5 Forecasting - Accuracy & Uncertainty

### Public Datasets and Benchmarks

**Packrift Packaging Optimization Benchmark Corpus**:
- **GitHub**: https://github.com/Packrift/packaging-optimization-benchmark-corpus
- **Dataset Release**: https://github.com/Packrift/packaging-optimization-benchmark-corpus/releases/tag/v2026.05.14
- **Live Corpus**: https://packrift.github.io/packaging-optimization-benchmark-corpus/
- **Focus**:
  - Packaging SKU retrieval and source-spec quality checks
  - Dimensional-weight, carton-fit, parcel/freight routing, and warehouse slotting benchmark pages
  - 1,000 exact-spec product records with 24 page types per SKU

### GitHub Project Collections

**Awesome Lists**:
- awesome-supply-chain
- awesome-logistics
- awesome-operations-research

## Full Systems

### Open Source WMS/ERP

#### ERPNext

**Description**: Open-source ERP system
- **GitHub**: https://github.com/frappe/erpnext
- **Website**: https://erpnext.com/
- **Modules**:
  - Inventory Management
  - Warehouse Management
  - Manufacturing
  - Purchase and Sales
  - Accounting

#### Odoo

**Description**: Open-source business apps
- **GitHub**: https://github.com/odoo/odoo
- **Website**: https://www.odoo.com/
- **Modules**:
  - Inventory
  - Manufacturing
  - Purchase
  - Sales
  - Warehouse

### Blockchain Supply Chain

#### Hyperledger Fabric Examples

**GitHub**: https://github.com/hyperledger/fabric-samples
- Supply chain traceability
- Food tracking
- Pharmaceutical tracking
- Asset transfer

#### TradeLens

**Description**: Blockchain-based shipping platform
- IBM and Maersk collaboration
- Container tracking
- Documentation digitization

## Academic Research Code

### Papers with Code

**Website**: https://paperswithcode.com/
- Search for "supply chain"
- Filter by task (forecasting, optimization, etc.)
- Find papers with available implementations

### GitHub Research Repositories

**Common Patterns**:
- University research groups
- Conference paper implementations
- Journal article code
- Thesis projects

## Learning Projects

### Beginner Projects

1. **ABC Analysis Tool**
   - Read inventory data
   - Calculate ABC classification
   - Visualize results

2. **EOQ Calculator**
   - Input: demand, costs
   - Calculate optimal order quantity
   - Sensitivity analysis

3. **Simple Demand Forecast**
   - Historical data
   - Moving average
   - Visualization

### Intermediate Projects

1. **Inventory Optimization System**
   - Multiple SKUs
   - Safety stock calculation
   - Reorder point logic
   - Dashboard

2. **Route Optimization Tool**
   - Multiple stops
   - Distance calculation
   - OR-Tools integration
   - Map visualization

3. **Warehouse Simulation**
   - Order arrival process
   - Worker resources
   - Performance metrics
   - What-if analysis

### Advanced Projects

1. **Demand Forecasting Pipeline**
   - Data preprocessing
   - Multiple models
   - Model selection
   - Deployment (API)

2. **Supply Chain Network Optimizer**
   - Multi-echelon network
   - Facility location
   - Flow optimization
   - Scenario analysis

3. **End-to-End Supply Chain System**
   - Forecasting module
   - Inventory planning
   - Transportation planning
   - Reporting dashboard

## Code Quality Best Practices

### Structure
- Modular design
- Clear function/class names
- Documentation (docstrings)
- Type hints (Python 3.5+)

### Testing
- Unit tests (pytest)
- Integration tests
- Test coverage >80%
- Continuous integration

### Documentation
- README with clear instructions
- Requirements.txt or environment.yml
- Usage examples
- API documentation

### Version Control
- Meaningful commit messages
- Branch strategy
- Pull request process
- Code review

## Resources for Learning

### Online Tutorials
- Real Python (supply chain tutorials)
- Towards Data Science (Medium)
- Analytics Vidhya
- KDnuggets

### Video Courses
- YouTube channels (Tech with Tim, Corey Schafer)
- Coursera supply chain courses
- edX operations research
- DataCamp

### Books with Code
- "Python for Data Analysis" (GitHub repo)
- "Forecasting: Principles and Practice" (R code)
- "Introduction to OR" (examples available)

---

**Last Updated**: October 2025
