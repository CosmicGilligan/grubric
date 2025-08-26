"""
Local embeddings module using sentence-transformers
Replaces OpenAI embedding functionality
"""

from sentence_transformers import SentenceTransformer
import numpy as np
from scipy.spatial.distance import cosine
import pickle
import os
from typing import List, Union
import pandas as pd

class LocalEmbeddings:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize the local embeddings model
        
        Args:
            model_name: Name of the sentence transformer model
                      Options:
                      - "all-MiniLM-L6-v2" (default, fast and good quality)
                      - "all-mpnet-base-v2" (higher quality, slower)
                      - "multi-qa-MiniLM-L6-cos-v1" (optimized for Q&A)
        """
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        print("Model loaded successfully!")
    
    def get_embedding(self, text: Union[str, List[str]]) -> Union[np.ndarray, List[np.ndarray]]:
        """
        Get embeddings for text(s)
        
        Args:
            text: Single text string or list of texts
            
        Returns:
            numpy array(s) containing embeddings
        """
        if isinstance(text, str):
            # Single text
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding
        else:
            # List of texts
            embeddings = self.model.encode(text, convert_to_numpy=True)
            return embeddings
    
    def calculate_similarity(self, embedding1: np.ndarray, embedding2: np.ndarray) -> float:
        """
        Calculate cosine similarity between two embeddings
        
        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector
            
        Returns:
            Similarity score (higher = more similar)
        """
        return 1 - cosine(embedding1, embedding2)
    
    def save_embeddings(self, embeddings: List[np.ndarray], filepath: str):
        """Save embeddings to disk"""
        with open(filepath, 'wb') as f:
            pickle.dump(embeddings, f)
    
    def load_embeddings(self, filepath: str) -> List[np.ndarray]:
        """Load embeddings from disk"""
        with open(filepath, 'rb') as f:
            return pickle.load(f)
    
    def search_docs(self, df: pd.DataFrame, user_query: str, top_n: int = 3, to_print: bool = True):
        """
        Search documents based on cosine similarity
        
        Args:
            df: DataFrame with 'text' and 'embeddings' columns
            user_query: Query text to search for
            top_n: Number of top results to return
            to_print: Whether to print results
            
        Returns:
            DataFrame with top matching documents and similarity scores
        """
        # Get embedding for the query
        query_embedding = self.get_embedding(user_query)
        
        # Calculate similarities
        df_similarities = df.copy()
        df_similarities["similarities"] = df.embeddings.apply(
            lambda x: self.calculate_similarity(query_embedding, x)
        )
        
        # Sort and get top results
        results = (
            df_similarities.sort_values("similarities", ascending=False)
            .head(top_n)
        )
        
        if to_print:
            print("Search Results:")
            for idx, row in results.iterrows():
                print(f"Similarity: {row['similarities']:.4f}")
                print(f"Text: {row['text'][:200]}...")
                print("-" * 50)
        
        return results

# Global instance - you can modify the model here
embedding_model = LocalEmbeddings("all-MiniLM-L6-v2")

# Convenience functions to match your existing API
def get_embedding(text: str) -> np.ndarray:
    """Drop-in replacement for OpenAI get_embedding function"""
    return embedding_model.get_embedding(text)

def calculate_similarity(embedding1: np.ndarray, embedding2: np.ndarray) -> float:
    """Drop-in replacement for your existing calculate_similarity function"""
    return embedding_model.calculate_similarity(embedding1, embedding2)

def search_docs(df: pd.DataFrame, user_query: str, top_n: int = 3, to_print: bool = True):
    """Drop-in replacement for your existing search_docs function"""
    return embedding_model.search_docs(df, user_query, top_n, to_print)