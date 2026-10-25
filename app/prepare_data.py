import os

import pandas as pd
from datasets import load_dataset

from config import KB_PATH, SAMPLE_SIZE

DATASET_NAME = "bitext/Bitext-customer-support-llm-chatbot-training-dataset"


def main():
    print("Downloading dataset...")
    df = load_dataset(DATASET_NAME, split="train").to_pandas()
    print(f"Full dataset: {len(df)} rows, columns: {list(df.columns)}")

    df = df[["instruction", "category", "intent", "response"]].dropna()
    df = df.drop_duplicates(subset="instruction")
    df["instruction"] = df["instruction"].str.strip()
    df["response"] = df["response"].str.strip()

    per_intent = max(1, SAMPLE_SIZE // df["intent"].nunique())
    parts = [g.sample(min(len(g), per_intent), random_state=42) for _, g in df.groupby("intent")]
    sample = pd.concat(parts).reset_index(drop=True)
    sample.insert(0, "id", range(len(sample)))

    os.makedirs(os.path.dirname(KB_PATH), exist_ok=True)
    sample.to_csv(KB_PATH, index=False, encoding="utf-8")

    print(f"Saved {len(sample)} rows to {KB_PATH}")
    print(f"Categories ({sample['category'].nunique()}): {sorted(sample['category'].unique())}")
    print(f"Intents: {sample['intent'].nunique()}")


if __name__ == "__main__":
    main()