import logging
import chromadb

def chroma_smoke_test():
    logging.basicConfig(level=logging.INFO)
    client = chromadb.HttpClient(host="chroma", port=8000)
    col = client.get_or_create_collection("docs")
    col.upsert(
        ids=["1", "2"],
        documents=["Кошка сидит на коврике.", "Собака любит гулять."],
        metadatas=[{"source": "test"}, {"source": "test"}],
    )
    logging.info("before query")
    res = col.query(query_texts=["кто любит гулять"], n_results=1)
    logging.info("CHROMA_SMOKE: doc=%s id=%s", res["documents"][0][0], res["ids"][0][0])
    logging.info("after query: %s", res)

chroma_smoke_test()
