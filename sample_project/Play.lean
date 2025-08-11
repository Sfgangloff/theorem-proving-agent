import Mathlib.Data.Set.Basic
import Mathlib.Tactic

open Set

def foo (s t : Set Nat) : Set Nat := s ∪ t

lemma foo_comm (s t : Set Nat) : foo s t = foo t s := by
  ext x
  simp [foo, or_comm]

lemma foo_assoc (s t u : Set Nat) : foo (foo s t) u = foo s (foo t u) := by
  ext x
  simp [foo, or_assoc]

lemma foo_idem (s : Set Nat) : foo s s = s := by
  ext x
  simp [foo]

lemma foo_empty_left (s : Set Nat) : foo (∅ : Set Nat) s = s := by
  ext x
  simp [foo]

lemma foo_empty_right (s : Set Nat) : foo s (∅ : Set Nat) = s := by
  ext x
  simp [foo]

lemma subset_left_foo (s t : Set Nat) : s ⊆ foo s t := by
  intro x hx
  exact Or.inl hx

lemma subset_right_foo (s t : Set Nat) : t ⊆ foo s t := by
  intro x hx
  exact Or.inr hx

lemma foo_subset_iff (s t u : Set Nat) : foo s t ⊆ u ↔ s ⊆ u ∧ t ⊆ u := by
  constructor
  · intro h
    constructor
    · intro x hx
      exact h (Or.inl hx)
    · intro x hx
      exact h (Or.inr hx)
  · intro h
    rcases h with ⟨hsu, htu⟩
    intro x hx
    rcases hx with hx | hx
    · exact hsu hx
    · exact htu hx

lemma foo_mono {s s' t t' : Set Nat} (hs : s ⊆ s') (ht : t ⊆ t') :
    foo s t ⊆ foo s' t' := by
  intro x hx
  rcases hx with hx | hx
  · exact Or.inl (hs hx)
  · exact Or.inr (ht hx)

lemma inter_foo_distrib_left (s t u : Set Nat) :
    s ∩ foo t u = foo (s ∩ t) (s ∩ u) := by
  ext x
  constructor
  · intro hx
    rcases hx with ⟨hs, htu⟩
    rcases htu with ht | hu
    · exact Or.inl ⟨hs, ht⟩
    · exact Or.inr ⟨hs, hu⟩
  · intro hx
    rcases hx with hst | hsu
    · rcases hst with ⟨hs, ht⟩
      exact ⟨hs, Or.inl ht⟩
    · rcases hsu with ⟨hs, hu⟩
      exact ⟨hs, Or.inr hu⟩

lemma foo_inter_distrib_right (s t u : Set Nat) :
    foo s t ∩ u = foo (s ∩ u) (t ∩ u) := by
  ext x
  constructor
  · intro hx
    rcases hx with ⟨hsu, hu⟩
    rcases hsu with hs | ht
    · exact Or.inl ⟨hs, hu⟩
    · exact Or.inr ⟨ht, hu⟩
  · intro hx
    rcases hx with hsu | htu
    · rcases hsu with ⟨hs, hu⟩
      exact ⟨Or.inl hs, hu⟩
    · rcases htu with ⟨ht, hu⟩
      exact ⟨Or.inr ht, hu⟩

lemma foo_univ_left (s : Set Nat) : foo (Set.univ) s = Set.univ := by
  ext x
  simp [foo]

lemma foo_univ_right (s : Set Nat) : foo s (Set.univ) = Set.univ := by
  ext x
  simp [foo]

-- New results extending the algebra of `foo` (union) on sets of naturals.

lemma subset_iff_foo_eq_right (s t : Set Nat) : s ⊆ t ↔ foo s t = t := by
  constructor
  · intro hst
    ext x
    constructor
    · intro hx
      rcases hx with hx | hx
      · exact hst hx
      · exact hx
    · intro hx
      exact Or.inr hx
  · intro hEq
    intro x hx
    have hx' : x ∈ foo s t := Or.inl hx
    simpa [foo, hEq] using hx'

lemma subset_iff_foo_eq_left (s t : Set Nat) : t ⊆ s ↔ foo s t = s := by
  constructor
  · intro hts
    ext x
    constructor
    · intro hx
      rcases hx with hx | hx
      · exact hx
      · exact hts hx
    · intro hx
      exact Or.inl hx
  · intro hEq
    intro x hx
    have hx' : x ∈ foo s t := Or.inr hx
    simpa [foo, hEq] using hx'

lemma foo_absorb_inter_left (s t : Set Nat) : foo s (s ∩ t) = s := by
  ext x
  constructor
  · intro hx
    rcases hx with hx | hx
    · exact hx
    · exact hx.1
  · intro hx
    exact Or.inl hx

lemma inter_foo_absorb_left (s t : Set Nat) : s ∩ foo s t = s := by
  ext x
  constructor
  · intro hx
    exact hx.1
  · intro hx
    exact ⟨hx, Or.inl hx⟩

lemma compl_foo (s t : Set Nat) : (foo s t)ᶜ = sᶜ ∩ tᶜ := by
  ext x
  simp [foo, not_or]

lemma diff_foo (s t u : Set Nat) : s \ foo t u = (s \ t) ∩ (s \ u) := by
  ext x
  constructor
  · intro hx
    rcases hx with ⟨hs, hnot⟩
    rcases not_or.mp hnot with ⟨hnt, hnu⟩
    exact ⟨⟨hs, hnt⟩, ⟨hs, hnu⟩⟩
  · intro hx
    rcases hx with ⟨⟨hs, hnt⟩, ⟨_, hnu⟩⟩
    exact ⟨hs, by exact not_or.mpr ⟨hnt, hnu⟩⟩

lemma nonempty_foo_iff (s t : Set Nat) : (foo s t).Nonempty ↔ s.Nonempty ∨ t.Nonempty := by
  constructor
  · intro h
    rcases h with ⟨x, hx⟩
    rcases hx with hs | ht
    · exact Or.inl ⟨x, hs⟩
    · exact Or.inr ⟨x, ht⟩
  · intro h
    rcases h with h | h
    · rcases h with ⟨x, hx⟩
      exact ⟨x, Or.inl hx⟩
    · rcases h with ⟨x, hx⟩
      exact ⟨x, Or.inr hx⟩