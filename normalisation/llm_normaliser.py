# normalisation/llm_normaliser.py
# Fallback: uses LLM (Claude API) to suggest mappings for XBRL tags
# that edgartools' built-in standardisation could not resolve.
#
# This module is NOT part of the main pipeline flow. It is invoked
# manually when concept_mapping.py encounters tags with a null
# standard_concept after edgartools processing. The LLM suggests a
# canonical mapping, which is reviewed by a human and, if accepted,
# written back to concept_map.json so future runs are deterministic.
