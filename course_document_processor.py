"""
Course document processor for rubric-based grading
Simplified version without PDF support - focuses on text files only
"""

import os
import pandas as pd
import numpy as np
import tiktoken
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import pickle
from typing import List, Tuple, Optional, Dict
import logging
from pathlib import Path

# Optional DOCX support
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DocxDocument = None
    DOCX_AVAILABLE = False

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CourseDocumentProcessor:
    def __init__(self, course_path: str, max_tokens: int = 500, embedding_model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize course document processor
        
        Args:
            course_path: Path to course documents (e.g., "../db/text/HIST109")
            max_tokens: Maximum tokens per chunk
            embedding_model_name: Sentence transformer model name
        """
        self.course_path = course_path
        self.max_tokens = max_tokens
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.embedding_model = None
        self.df = None
        
        # Load embedding model
        try:
            self.embedding_model = SentenceTransformer(embedding_model_name)
            logger.info(f"Loaded embedding model: {embedding_model_name}")
        except Exception as e:
            logger.error(f"Error loading embedding model: {e}")
    
    def get_embedding_filename(self, course_name: str) -> str:
        """Get course-specific embedding filename"""
        safe_course_name = course_name.replace("/", "_").replace(" ", "_")
        return f'embeddings_{safe_course_name}.pkl'
    
    def clean_text(self, text: str) -> str:
        """Clean text by removing excess whitespace and newlines"""
        if not text:
            return ""
        text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        text = ' '.join(text.split())
        return text
    
    def split_into_chunks(self, text: str, filename: str = "") -> List[Dict]:
        """Split text into smaller chunks with metadata"""
        if not text or not text.strip():
            return []
            
        paragraphs = text.split('\n\n')
        chunks = []
        
        for para_idx, paragraph in enumerate(paragraphs):
            if not paragraph.strip():
                continue
                
            paragraph = paragraph.strip()
            token_count = len(self.tokenizer.encode(paragraph))
            
            if token_count <= self.max_tokens:
                chunks.append({
                    'text': paragraph,
                    'filename': filename,
                    'chunk_id': para_idx,
                    'n_tokens': token_count
                })
            else:
                # Split large paragraphs into sentences
                sentences = paragraph.split('. ')
                current_chunk = []
                current_tokens = 0
                chunk_id = 0
                
                for sentence in sentences:
                    if not sentence.strip():
                        continue
                        
                    sentence_tokens = len(self.tokenizer.encode(sentence))
                    
                    if current_tokens + sentence_tokens > self.max_tokens:
                        if current_chunk:
                            chunk_text = '. '.join(current_chunk) + '.'
                            chunks.append({
                                'text': chunk_text,
                                'filename': filename,
                                'chunk_id': f"{para_idx}_{chunk_id}",
                                'n_tokens': len(self.tokenizer.encode(chunk_text))
                            })
                            chunk_id += 1
                        current_chunk = [sentence]
                        current_tokens = sentence_tokens
                    else:
                        current_chunk.append(sentence)
                        current_tokens += sentence_tokens
                
                if current_chunk:
                    chunk_text = '. '.join(current_chunk)
                    if not chunk_text.endswith('.'):
                        chunk_text += '.'
                    chunks.append({
                        'text': chunk_text,
                        'filename': filename,
                        'chunk_id': f"{para_idx}_{chunk_id}",
                        'n_tokens': len(self.tokenizer.encode(chunk_text))
                    })
        
        return chunks
    
    def extract_docx_text(self, docx_path: str) -> str:
        """Extract text from Word document (if docx available)"""
        if not DOCX_AVAILABLE or DocxDocument is None:
            logger.warning(f"python-docx not installed. Skipping DOCX file: {docx_path}")
            return ""
        
        try:
            doc = DocxDocument(docx_path)
            text = ""
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text += paragraph.text + "\n"
            return text
        except Exception as e:
            logger.error(f"Error extracting docx text from {docx_path}: {e}")
            return ""
    
    def crawl_course_documents(self) -> List[Dict]:
        """Crawl course directory and extract all document chunks"""
        if not os.path.exists(self.course_path):
            logger.error(f"Course path does not exist: {self.course_path}")
            return []
        
        all_chunks = []
        supported_extensions = ['.txt', '.md', '.lec']
        
        # Add DOCX only if library is available
        if DOCX_AVAILABLE:
            supported_extensions.append('.docx')
        
        logger.info(f"Supported file types: {', '.join(supported_extensions)}")
        
        for root, dirs, files in os.walk(self.course_path):
            for file in files:
                if any(file.lower().endswith(ext) for ext in supported_extensions):
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, self.course_path)
                    
                    try:
                        content = ""
                        if file.lower().endswith('.docx'):
                            if DOCX_AVAILABLE:
                                content = self.extract_docx_text(file_path)
                            else:
                                logger.warning(f"Skipping DOCX file (python-docx not installed): {relative_path}")
                                continue
                        else:
                            # Handle text files
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    content = f.read()
                            except UnicodeDecodeError:
                                try:
                                    with open(file_path, 'r', encoding='latin-1') as f:
                                        content = f.read()
                                except Exception as e:
                                    logger.warning(f"Could not read {relative_path}: {e}")
                                    continue
                        
                        if content and len(content.strip()) > 50:
                            content = self.clean_text(content)
                            file_chunks = self.split_into_chunks(content, relative_path)
                            all_chunks.extend(file_chunks)
                            logger.info(f"Processed {relative_path}: {len(file_chunks)} chunks")
                    
                    except Exception as e:
                        logger.warning(f"Error processing {file_path}: {e}")
        
        logger.info(f"Total chunks extracted: {len(all_chunks)}")
        return all_chunks
    
    def create_embeddings(self, chunks: List[Dict]) -> pd.DataFrame:
        """Create embeddings for all document chunks"""
        if not self.embedding_model:
            logger.error("Embedding model not loaded")
            return pd.DataFrame()
        
        if not chunks:
            logger.warning("No chunks provided for embedding creation")
            return pd.DataFrame()
        
        df = pd.DataFrame(chunks)
        
        # Create embeddings in batches for efficiency
        texts = df['text'].tolist()
        logger.info(f"Creating embeddings for {len(texts)} chunks...")
        
        try:
            # Create embeddings using sentence transformer
            embeddings_array = self.embedding_model.encode(
                texts, 
                convert_to_tensor=False, 
                show_progress_bar=True,
                batch_size=32
            )
            
            # Convert to list of numpy arrays for consistent handling
            df['embeddings'] = [np.array(emb, dtype=np.float32) for emb in embeddings_array]
            logger.info("Embeddings created successfully")
            
        except Exception as e:
            logger.error(f"Error creating embeddings: {e}")
            # Create empty embeddings as fallback
            df['embeddings'] = [np.array([], dtype=np.float32) for _ in range(len(df))]
        
        return df
    
    def save_embeddings(self, df: pd.DataFrame, course_name: str):
        """Save embeddings to pickle file"""
        if df.empty:
            logger.warning("No embeddings to save")
            return
            
        filename = self.get_embedding_filename(course_name)
        try:
            df.to_pickle(filename)
            logger.info(f"Embeddings saved to {filename}")
        except Exception as e:
            logger.error(f"Error saving embeddings: {e}")
    
    def load_embeddings(self, course_name: str) -> Optional[pd.DataFrame]:
        """Load embeddings from pickle file"""
        filename = self.get_embedding_filename(course_name)
        try:
            if os.path.exists(filename):
                df = pd.read_pickle(filename)
                logger.info(f"Embeddings loaded from {filename}")
                return df
        except Exception as e:
            logger.error(f"Error loading embeddings: {e}")
        return None
    
    def load_or_create_embeddings(self, course_name: str, force_refresh: bool = False) -> pd.DataFrame:
        """Load existing embeddings or create new ones"""
        if not force_refresh:
            df = self.load_embeddings(course_name)
            if df is not None and len(df) > 0:
                self.df = df
                return df
        
        # Create new embeddings
        logger.info(f"Creating new embeddings for course: {course_name}")
        chunks = self.crawl_course_documents()
        
        if chunks:
            df = self.create_embeddings(chunks)
            if len(df) > 0:
                self.save_embeddings(df, course_name)
                self.df = df
                return df
        
        logger.warning("No documents found or processed")
        self.df = pd.DataFrame()
        return self.df
    
    def search_documents(self, query: str, top_k: int = 5, similarity_threshold: float = 0.1) -> pd.DataFrame:
        """Search for relevant documents based on query"""
        if self.df is None or len(self.df) == 0:
            return pd.DataFrame()
        
        if not self.embedding_model:
            return pd.DataFrame()
        
        try:
            # Get query embedding
            query_embedding = self.embedding_model.encode([query], convert_to_tensor=False)[0]
            query_embedding = np.array(query_embedding, dtype=np.float32)
            
            # Filter valid embeddings
            valid_embeddings = self.df['embeddings'].apply(
                lambda x: isinstance(x, np.ndarray) and len(x) > 0
            )
            
            if not valid_embeddings.any():
                return pd.DataFrame()
            
            valid_df = self.df[valid_embeddings].copy()
            
            # Convert embeddings to proper numpy array format
            doc_embeddings_list = []
            for emb in valid_df['embeddings']:
                if isinstance(emb, np.ndarray) and len(emb) > 0:
                    doc_embeddings_list.append(emb.astype(np.float32))
                else:
                    # Skip invalid embeddings
                    continue
            
            if not doc_embeddings_list:
                return pd.DataFrame()
            
            # Stack embeddings into 2D array
            doc_embeddings = np.stack(doc_embeddings_list)
            
            # Ensure query_embedding is 2D for cosine_similarity
            query_embedding_2d = query_embedding.reshape(1, -1)
            
            # Calculate similarities
            similarities = cosine_similarity(query_embedding_2d, doc_embeddings)[0]
            
            # Update valid_df with similarities (only for rows with valid embeddings)
            valid_indices = valid_df.index[valid_df['embeddings'].apply(
                lambda x: isinstance(x, np.ndarray) and len(x) > 0
            )].tolist()
            
            if len(valid_indices) != len(similarities):
                logger.warning("Mismatch between valid embeddings and similarities")
                return pd.DataFrame()
            
            # Create a new dataframe with similarities
            result_df = valid_df.loc[valid_indices].copy()
            result_df['similarity'] = similarities
            
            # Filter by threshold and return top results
            relevant_docs = result_df[result_df['similarity'] >= similarity_threshold]
            
            if len(relevant_docs) == 0:
                return pd.DataFrame()
            
            return relevant_docs.nlargest(top_k, 'similarity')
        
        except Exception as e:
            logger.error(f"Error in document search: {e}")
            return pd.DataFrame()
    
    def get_document_context(self, query: str, max_context_length: int = 2000) -> str:
        """Get formatted document context for a query"""
        relevant_docs = self.search_documents(query, top_k=5)
        
        if len(relevant_docs) == 0:
            return "No relevant course documents found for this query."
        
        context = "RELEVANT COURSE MATERIALS:\n\n"
        current_length = len(context)
        
        for _, doc in relevant_docs.iterrows():
            doc_text = f"Source: {doc['filename']} (Relevance: {doc['similarity']:.3f})\n"
            doc_text += f"Content: {doc['text']}\n\n"
            
            if current_length + len(doc_text) > max_context_length:
                break
            
            context += doc_text
            current_length += len(doc_text)
        
        return context
    
    def get_course_statistics(self) -> Dict:
        """Get statistics about the course document collection"""
        if self.df is None or len(self.df) == 0:
            return {
                'total_chunks': 0,
                'unique_documents': 0,
                'avg_tokens_per_chunk': 0,
                'total_tokens': 0,
                'document_types': {}
            }
        
        stats = {
            'total_chunks': len(self.df),
            'unique_documents': self.df['filename'].nunique(),
            'avg_tokens_per_chunk': self.df['n_tokens'].mean(),
            'total_tokens': self.df['n_tokens'].sum(),
            'document_types': {}
        }
        
        # Count by file type
        for filename in self.df['filename'].unique():
            ext = os.path.splitext(filename)[1].lower()
            if ext in stats['document_types']:
                stats['document_types'][ext] += 1
            else:
                stats['document_types'][ext] = 1
        
        return stats

# Helper functions for backward compatibility
def load_course_embeddings(course_path: str, course_name: str, force_refresh: bool = False) -> CourseDocumentProcessor:
    """Load course embeddings with backward compatibility"""
    processor = CourseDocumentProcessor(course_path)
    processor.load_or_create_embeddings(course_name, force_refresh)
    return processor

def search_course_materials(processor: CourseDocumentProcessor, query: str, top_k: int = 5) -> str:
    """Search course materials and return formatted context"""
    return processor.get_document_context(query, max_context_length=2000)