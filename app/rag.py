import sys

import chromadb
import ollama
import pandas as pd

from config import CHROMA_PATH, COLLECTION_NAME, EMBED_MODEL, KB_PATH, OLLAMA_HOST

client = ollama.Client(host=OLLAMA_HOST)


def embed(texts):
    return client.embed(model=EMBED_MODEL, input=texts)["embeddings"]


def get_collection(reset=False):
    db = chromadb.PersistentClient(path=CHROMA_PATH)
    if reset:
        try:
            db.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    return db.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def build_index(batch_size=32):
    df = pd.read_csv(KB_PATH)
    col = get_collection(reset=True)
    total = len(df)
    for start in range(0, total, batch_size):
        chunk = df.iloc[start:start + batch_size]
        questions = chunk["instruction"].tolist()
        col.add(
            ids=[str(i) for i in chunk["id"]],
            documents=questions,
            embeddings=embed(questions),
            metadatas=[
                {"category": r.category, "intent": r.intent, "response": r.response}
                for r in chunk.itertuples()
            ],
        )
        print(f"Indexed {min(start + batch_size, total)}/{total}")
    print("Done.")


def retrieve(query, k=4, category=None):
    col = get_collection()
    kwargs = {"where": {"category": category}} if category else {}
    res = col.query(query_embeddings=embed([query]), n_results=k, **kwargs)
    hits = []
    for i in range(len(res["ids"][0])):
        meta = res["metadatas"][0][i]
        hits.append({
            "id": res["ids"][0][i],
            "question": res["documents"][0][i],
            "category": meta["category"],
            "intent": meta["intent"],
            "response": meta["response"],
            "score": round(1 - res["distances"][0][i], 4),
        })
    return hits


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build_index()
    else:
        query = " ".join(sys.argv[1:]) or "where is my package"
        for hit in retrieve(query):
            print(f"[{hit['score']}] {hit['category']}/{hit['intent']}")
            print(f"   Q: {hit['question']}")
            print(f"   A: {hit['response'][:120]}...\n")