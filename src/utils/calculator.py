"""Module calculatrice — volontairement cassé pour test CodeAgent."""


def add(a: int, b: int) -> int:
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b


def multiply(a: int, b: int) -> int:
    return a * b


def divide(a: int, b: int) -> float:
    if b == 0:
        raise ZeroDivisionError("Division par zéro impossible.")
    return a / b


def factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def fibonacci(n: int) -> list[int]:
    if n <= 0:
        return []
    if n == 1:
        return [0]
    result = [0, 1]
    for i in range(2, n):
        result.append(result[i - 1] + result[i - 2])
    return result


def average(numbers: list[int]) -> float:
    if not numbers:
        raise ValueError("La liste ne peut pas être vide.")
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True


class Calculator:
    def __init__(self):
        self.history = []

    def compute(self, operation: str, a: int, b: int) -> float:
        ops = {
            "add": add,
            "subtract": subtract,
            "multiply": multiply,
            "divide": divide
        }
        if operation not in ops:
            raise ValueError(f"Opération '{operation}' non supportée. Utilisez {list(ops.keys())}")
        result = ops[operation](a, b)
        self.history.append(result)
        return result

    def last_result(self):
        if not self.history:
            return None
        return self.history[-1]

    def clear(self):
        self.history = []
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
