"""CVM ZIP parser contract regressions."""
from macro_b3_bot.adapters.cvm.zip_reader import CvmZipReader


def test_statement_type_is_derived_from_filename() -> None:
    assert CvmZipReader._statement_type_from_filename(
        "itr_cia_aberta_DRE_con_2026.csv"
    ) == "DRE"
    assert CvmZipReader._statement_type_from_filename(
        "dfp_cia_aberta_BPP_ind_2025.csv"
    ) == "BPP"
    assert CvmZipReader._statement_type_from_filename(
        "itr_cia_aberta_2026.csv"
    ) is None


def test_cvm_code_leading_zeroes_are_normalized() -> None:
    assert CvmZipReader._normalize_cvm_code("001023") == "1023"
    assert CvmZipReader._normalize_cvm_code("000000") == "0"
