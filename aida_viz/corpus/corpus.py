"""
Contains utilities for reading from the LDC corpus used for AIDA.
"""
import hashlib
import io
import re
from typing import Mapping, Optional, Pattern
from zipfile import Path as ZipPath
from zipfile import ZipFile

import pandas as pd
from immutablecollections import ImmutableDict, immutabledict
from tqdm import tqdm
from vistautils.io_utils import CharSource


def get_text_docs(corpus_zipfile: ZipFile) -> ImmutableDict[str, str]:
    print(f"Reading .ltf documents in {corpus_zipfile.filename}")

    prefix = get_root_dir_name(corpus_zipfile) or ""

    parent_children_path = _find_name_in_zip(
        corpus_zipfile, re.compile(f"{prefix}docs/parent_children.tab")
    )

    if not parent_children_path:
        raise RuntimeError("Archive lacks parent_children.tab")

    parent_children_tab = _read_tab_file(
        CharSource.from_file_in_zip(corpus_zipfile, parent_children_path)
    )

    child_to_parent_map = _create_child_to_parent_map(parent_children_tab)

    text_docs = {}
    text_dir = ZipPath(corpus_zipfile, at="data/ltf/")

    for source_doc_path in text_dir.iterdir():
        source_doc_zip = ZipFile(io.BytesIO(source_doc_path.read_bytes()))

        for source_info in tqdm(
            source_doc_zip.infolist(),
            desc=f"Extracting {source_doc_path.name}",
            bar_format="{l_bar}{bar:20}{r_bar}",
        ):

            doc = ZipPath(source_doc_zip, at=source_info.filename)
            try:
                doceid = doc.name.split(".")[0]
                doc_id = child_to_parent_map[doceid]
                text_docs[doc_id] = convert_ltf_to_raw_text(
                    doc.read_text(encoding="utf-8")
                )
            except AttributeError:
                raise FileNotFoundError(f"Could not read from {doc}.")

    return immutabledict(text_docs)


def convert_ltf_to_raw_text(xml_string: str) -> str:
    """
    Converts LDC LTF format to the corresponding original source text.
    Throws an LtfConversionError if problems are encountered.
    """
    last_ofs = 0
    raw_out = ""
    start_ch, end_ch, raw_ch_count = 0, 0, 0
    raw_cksum = ""

    for line in xml_string.split("\n"):
        if "<DOC " in line:
            match = re.match(
                r".*?raw_text_char_length=\"(\d+)\" raw_text_md5=\"(\w+)\".*?", line
            )
            if match:
                raw_ch_count = int(match.group(1))
                raw_cksum = match.group(2)
            else:
                raise LtfConversionError("DOC tag malformed. First line is: " + line)
        else:
            match = re.match(r"<SEG .*?start_char=\"(\d+)\" end_char=\"(\d+)\"", line)
            if match:
                start_ch, end_ch = int(match.group(1)), int(match.group(2))
            else:
                match = re.match(r"<ORIGINAL_TEXT>(.*?)<", line)
                if match:
                    otxt = match.group(1)
                    otxt = re.sub(r"&lt;", r"<", otxt)
                    otxt = re.sub(r"&gt;", r">", otxt)
                    otxt = re.sub(r"&amp;", r"&", otxt)
                    if start_ch > last_ofs:
                        raw_out += "\n" * (start_ch - last_ofs)
                    raw_out += otxt + "\n"
                    last_ofs = end_ch + 2

    while len(raw_out) < raw_ch_count:
        raw_out += "\n"
    new_cksum = hashlib.md5(raw_out.encode("utf8")).hexdigest()

    if new_cksum != raw_cksum:
        raise LtfConversionError(
            "Checksum mismatch in LTF conversion: "
            "expecting {!s}, got {!s}".format(raw_cksum, new_cksum)
        )
    return raw_out


def get_root_dir_name(zip_file: ZipFile) -> Optional[str]:
    """
    Given a zip file, gets the single top-level directory it contains.

    This directory name will include a trailing /

    Throws an `RuntimeError` if there is no top-level directory or if there is more than one.
    """
    zip_infos = zip_file.infolist()
    if zip_infos:
        first_entry_name = zip_infos[0].filename
        if "/" in first_entry_name:
            slash_index = first_entry_name.index("/")
        else:
            return None

        top_level_directory = first_entry_name[0 : slash_index + 1]

        for zip_info in zip_infos:
            if not zip_info.filename.startswith(top_level_directory):
                return None

        return top_level_directory
    else:
        return None


def _find_name_in_zip(zip_file: ZipFile, name_regex: Pattern) -> Optional[str]:
    """
    Give a regular expression, will find the first entry name in the zip file which matches it.

    If not such name is found, returns `None`.
    """
    for name in zip_file.namelist():
        if name_regex.match(name):
            return name
    return None


def _read_tab_file(tab_file_source: CharSource) -> pd.DataFrame:
    """Read a tab-delimited file in to a Pandas DataFrame."""
    with tab_file_source.open() as tab_file:
        return pd.read_csv(tab_file, sep="\t", encoding="utf-8")


def _create_child_to_parent_map(
    parent_child_file_df: pd.DataFrame
) -> Mapping[str, str]:
    """Using the `docs/parent_children.tab` file from an AIDA corpus, creating a mapping from
    child to parent documents.
    """
    return immutabledict(
        [
            (row["child_uid"], row["parent_uid"])
            for _, row in parent_child_file_df.iterrows()
        ]
    )


class LtfConversionError(Exception):
    pass
