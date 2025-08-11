import Mathlib.Data.Set.Basic

open Set

def foo (s t : Set Nat) : Set Nat := s ∪ t

lemma foo_comm (s t : Set Nat) : foo s t = foo t s := by
  ext x
  simp [foo, Set.mem_union, Or.comm]