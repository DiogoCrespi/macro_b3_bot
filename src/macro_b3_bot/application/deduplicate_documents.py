from __future__ import annotations

from typing import Dict, Any, List, Tuple
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

def _compute_jaccard_similarity(text1: str, text2: str) -> float:
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)

class IpeDeduplicationPipeline:
    """
    Pipeline de deduplicação em 3 níveis (Arquivo Idêntico, Texto Idêntico, Quase Duplicado Lexical).
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def deduplicate_extracted_batch(self, limit: int = 500) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        rows = conn.execute("""
            SELECT document_id, document_checksum, normalized_text_checksum, extracted_text
            FROM extracted_documents
            LIMIT ?
        """, [limit]).fetchall()

        exact_file_dups = 0
        exact_text_dups = 0
        near_dups = 0

        seen_files: Dict[str, str] = {}
        seen_texts: Dict[str, str] = {}
        text_corpus: List[Tuple[str, str]] = [] # (doc_id, text)

        for doc_id, doc_hash, text_hash, text in rows:
            # Nível 1: Hash de Arquivo Idêntico
            if doc_hash in seen_files:
                canonical_id = seen_files[doc_hash]
                store.save_duplicate_link({
                    "canonical_document_id": canonical_id,
                    "duplicate_document_id": doc_id,
                    "duplicate_type": "EXACT_FILE_DUPLICATE",
                    "similarity": 1.0
                })
                exact_file_dups += 1
                continue
            else:
                seen_files[doc_hash] = doc_id

            # Nível 2: Hash de Texto Normalizado Idêntico
            if text_hash in seen_texts:
                canonical_id = seen_texts[text_hash]
                store.save_duplicate_link({
                    "canonical_document_id": canonical_id,
                    "duplicate_document_id": doc_id,
                    "duplicate_type": "EXACT_TEXT_DUPLICATE",
                    "similarity": 1.0
                })
                exact_text_dups += 1
                continue
            else:
                seen_texts[text_hash] = doc_id

            # Nível 3: Quase Duplicado Lexical (Jaccard >= 0.95)
            is_near_dup = False
            for prev_id, prev_text in text_corpus:
                sim = _compute_jaccard_similarity(text, prev_text)
                if sim >= 0.95:
                    store.save_duplicate_link({
                        "canonical_document_id": prev_id,
                        "duplicate_document_id": doc_id,
                        "duplicate_type": "NEAR_DUPLICATE",
                        "similarity": round(sim, 4)
                    })
                    near_dups += 1
                    is_near_dup = True
                    break

            if not is_near_dup:
                text_corpus.append((doc_id, text))
                conn.execute(
                    "UPDATE ipe_processing_queue SET status = 'DEDUPLICATED', updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                    [doc_id]
                )

        store.close()

        return {
            "total_processed": len(rows),
            "exact_file_duplicates": exact_file_dups,
            "exact_text_duplicates": exact_text_dups,
            "near_duplicates": near_dups,
            "canonical_documents": len(text_corpus)
        }
