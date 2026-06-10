"""Metaheuristics: iterated local search, simulated annealing and a
genetic algorithm.

Split out of server.py for maintainability. Availability flags, solver
library modules and sibling helpers still live in (or are re-exported
from) `server`, so functions here resolve them with call-time
`from server import ...` / `import server` — late binding keeps the lazy
solver loaders and `monkeypatch.setattr(server, ...)` in tests working.
Never import `server` at module level here: this module is imported while
server.py is still executing.
"""
from __future__ import annotations

import logging
from typing import List

from solvers.heuristics import (
    _indices_by_identity,
    calculate_route_distance,
    nearest_neighbor_optimize,
)
from solvers.local_search import or_opt_improve, two_opt_improve

logger = logging.getLogger("server")

def iterated_local_search(
    stops: List[dict],
    cost_matrix: List[List[float]],
    start_index: int = 0,
    time_limit_seconds: float = 10.0,
) -> List[dict]:
    """Iterated Local Search with double-bridge perturbation.

    Significantly outperforms SA/GA because:
    - Uses structured double-bridge kicks (not random swaps) to escape local minima
    - Applies Or-Opt + 2-opt after every perturbation (deep local search)
    - Accepts only improving moves (no random acceptance) → always moves toward better solutions

    Time complexity: O(n^2) per local search pass × number of restarts in time budget.
    """
    import time
    import random

    n = len(stops)
    if n <= 3:
        return stops

    def _local_search(route: List[int]) -> List[int]:
        """2-opt + Or-Opt pass until no improvement."""
        r = two_opt_improve(route, cost_matrix)
        r = or_opt_improve(r, cost_matrix)
        return r

    def _double_bridge(route: List[int]) -> List[int]:
        """Double-bridge 4-opt move: split into A|B|C|D → A|C|B|D.
        Keeps depot fixed at position 0. Creates crossings that 2-opt cannot undo,
        enabling escape from deep local minima."""
        if len(route) < 6:
            # Not enough nodes for a meaningful double-bridge — do a segment reversal instead
            i, j = sorted(random.sample(range(1, len(route)), 2))
            r = route[:]
            r[i:j] = reversed(r[i:j])
            return r
        # Pick 3 cut points inside the route (after the fixed depot at index 0)
        positions = sorted(random.sample(range(1, len(route)), 3))
        a, b, c = positions
        seg_A = route[:a]
        seg_B = route[a:b]
        seg_C = route[b:c]
        seg_D = route[c:]
        return seg_A + seg_C + seg_B + seg_D

    # Seed: nearest-neighbour → local search
    nn_result = nearest_neighbor_optimize(stops, cost_matrix, start_index)
    current = _local_search(_indices_by_identity(stops, nn_result))
    best = current[:]
    best_cost = calculate_route_distance(best, cost_matrix)

    deadline = time.monotonic() + time_limit_seconds
    restarts = 0
    while time.monotonic() < deadline:
        candidate = _local_search(_double_bridge(current[:]))
        candidate_cost = calculate_route_distance(candidate, cost_matrix)
        # Always accept improvements; keep best ever seen
        if candidate_cost < calculate_route_distance(current, cost_matrix):
            current = candidate
        if candidate_cost < best_cost:
            best = candidate[:]
            best_cost = candidate_cost
        restarts += 1

    return [stops[i] for i in best]

def simulated_annealing_optimize(stops: List[dict], distance_matrix: List[List[float]], 
                                  start_index: int = 0, iterations: int = 10000) -> List[dict]:
    """Simulated Annealing optimization - probabilistic meta-heuristic"""
    import random
    import math
    
    n = len(stops)
    if n <= 2:
        return stops
    
    # Start with nearest neighbor solution
    current = list(range(n))
    if start_index != 0:
        current.remove(start_index)
        current = [start_index] + current
    
    current_dist = calculate_route_distance(current, distance_matrix)
    best = current[:]
    best_dist = current_dist
    
    temperature = 100.0
    cooling_rate = 0.9995
    
    for _ in range(iterations):
        # Generate neighbor by swapping two random positions (keep start fixed)
        i, j = random.sample(range(1, n), 2)
        neighbor = current[:]
        neighbor[i], neighbor[j] = neighbor[j], neighbor[i]
        
        neighbor_dist = calculate_route_distance(neighbor, distance_matrix)
        delta = neighbor_dist - current_dist
        
        # Accept better solutions or worse with probability
        if delta < 0 or random.random() < math.exp(-delta / temperature):
            current = neighbor
            current_dist = neighbor_dist
            
            if current_dist < best_dist:
                best = current[:]
                best_dist = current_dist
        
        temperature *= cooling_rate
    
    return [stops[i] for i in best]

def genetic_algorithm_optimize(stops: List[dict], distance_matrix: List[List[float]], 
                               start_index: int = 0, generations: int = 200, 
                               population_size: int = 50) -> List[dict]:
    """Genetic Algorithm optimization - evolutionary meta-heuristic"""
    import random
    
    n = len(stops)
    if n <= 2:
        return stops
    
    def create_individual():
        """Create a random route keeping start_index first"""
        route = list(range(n))
        route.remove(start_index)
        random.shuffle(route)
        return [start_index] + route
    
    def fitness(individual):
        """Lower distance = higher fitness"""
        return 1.0 / (1.0 + calculate_route_distance(individual, distance_matrix))
    
    def crossover(parent1, parent2):
        """Order crossover (OX)"""
        size = len(parent1)
        start, end = sorted(random.sample(range(1, size), 2))
        
        child = [None] * size
        child[0] = start_index
        child[start:end] = parent1[start:end]
        
        remaining = [x for x in parent2 if x not in child]
        idx = 0
        for i in range(size):
            if child[i] is None:
                child[i] = remaining[idx]
                idx += 1
        
        return child
    
    def mutate(individual, rate=0.1):
        """Swap mutation"""
        if random.random() < rate and len(individual) > 2:
            i, j = random.sample(range(1, len(individual)), 2)
            individual[i], individual[j] = individual[j], individual[i]
        return individual
    
    # Initialize population
    population = [create_individual() for _ in range(population_size)]
    
    for _ in range(generations):
        # Selection (tournament)
        new_population = []
        
        # Elitism - keep best
        population.sort(key=fitness, reverse=True)
        new_population.append(population[0][:])
        
        while len(new_population) < population_size:
            # Tournament selection
            tournament = random.sample(population, 5)
            parent1 = max(tournament, key=fitness)
            tournament = random.sample(population, 5)
            parent2 = max(tournament, key=fitness)
            
            child = crossover(parent1, parent2)
            child = mutate(child)
            new_population.append(child)
        
        population = new_population
    
    # Return best individual
    best = max(population, key=fitness)
    return [stops[i] for i in best]
