import os
import fitz  # PyMuPDF
from langchain_huggingface import HuggingFaceEmbeddings
import chromadb
import traceback
from tqdm import tqdm  # For progress bars
import gc  # Garbage collection

# --- Configuration ---
PDF_FILE_PATH = r"C:\Users\brend\CI-16000.74-International-Conventions.pdf"  # Replace with your PDF file path
CHROMA_DB_PATH = "rag_chroma_db"  # Directory to store ChromaDB
CHUNK_SIZE = 50  # Smaller chunk size
CHUNK_OVERLAP = 10  # Smaller overlap
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # More reliable model
MAX_PAGE_LENGTH = 100000  # Truncate very large pages to avoid memory issues

def process_page(page_num, pdf_document):
    """Process a single page and return its text"""
    try:
        page = pdf_document[page_num]
        text = ""
        
        try:
            # Try the standard method first
            text = page.get_text()
        except Exception:
            pass
        
        # If that fails or returns empty, try alternate methods
        if not text:
            try:
                # Method 2: Try with different parameters
                text = page.get_text("text")
            except Exception:
                pass
        
        # If still empty, try blocks extraction
        if not text:
            try:
                # Method 3: Extract text blocks
                blocks = page.get_text("blocks")
                text = "\n".join([b[4] for b in blocks if isinstance(b, tuple) and len(b) > 4])
            except Exception:
                pass
        
        # Truncate extremely large pages to avoid memory issues
        if len(text) > MAX_PAGE_LENGTH:
            print(f"Warning: Page {page_num + 1} text is very large ({len(text)} chars), truncating to {MAX_PAGE_LENGTH}")
            text = text[:MAX_PAGE_LENGTH]
        
        # If text is still empty after all methods, log it
        if not text.strip():
            print(f"Warning: Page {page_num + 1} appears to be empty or unreadable")
            return ""
            
        return text
        
    except Exception as e:
        print(f"Error processing page {page_num + 1}: {str(e)}")
        traceback.print_exc()
        return ""

def create_and_store_chunks(text, page_num, client, collection):
    """Create chunks from text and store them immediately"""
    if not text.strip():
        return 0
        
    chunk_count = 0
    start_index = 0
    text_length = len(text)
    batch_chunks = []
    batch_ids = []
    
    # Fixed while loop to prevent infinite loop conditions
    while start_index < text_length:
        try:
            # Calculate end index for this chunk
            end_index = min(start_index + CHUNK_SIZE, text_length)
            
            # Check if we've reached the end
            if start_index >= end_index:
                break
                
            chunk = text[start_index:end_index]
            
            # Only process non-empty chunks
            if chunk.strip():
                batch_chunks.append(chunk)
                batch_ids.append(f"p{page_num+1}_c{chunk_count}")
                chunk_count += 1
                
                # Process in small batches to save memory
                if len(batch_chunks) >= 10:
                    embeddings_model = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
                    batch_embeddings = embeddings_model.embed_documents(batch_chunks)
                    
                    collection.add(
                        ids=batch_ids,
                        embeddings=batch_embeddings,
                        documents=batch_chunks
                    )
                    
                    # Clear for next batch
                    batch_chunks = []
                    batch_ids = []
                    # Force garbage collection
                    del batch_embeddings
                    gc.collect()
            
            # Move start_index forward, ensuring we make progress
            # Ensure it moves at least 1 character forward to prevent infinite loops
            next_start = end_index - CHUNK_OVERLAP
            if next_start <= start_index:
                next_start = start_index + 1  # Force progress by at least 1 character
            
            start_index = next_start
            
        except MemoryError:
            print(f"Memory error processing chunk on page {page_num+1}, skipping rest of page...")
            break
        except Exception as e:
            print(f"Error processing chunk: {str(e)}")
            traceback.print_exc()
            # Force progress to avoid infinite loop in case of repeated errors
            start_index += CHUNK_SIZE // 2  # Skip ahead by half a chunk
            continue
    
    # Process any remaining chunks
    if batch_chunks:
        try:
            embeddings_model = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
            batch_embeddings = embeddings_model.embed_documents(batch_chunks)
            
            collection.add(
                ids=batch_ids,
                embeddings=batch_embeddings,
                documents=batch_chunks
            )
            
            del batch_embeddings
            gc.collect()
        except Exception as e:
            print(f"Error processing final batch: {str(e)}")
            traceback.print_exc()
    
    return chunk_count

if __name__ == "__main__":
    # Install tqdm if not already installed
    try:
        from tqdm import tqdm
    except ImportError:
        print("Installing tqdm package for progress bars...")
        import subprocess
        subprocess.check_call(["pip", "install", "tqdm"])
        from tqdm import tqdm
    
    # Setup ChromaDB
    os.makedirs(CHROMA_DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    
    # Check if collection exists and recreate if needed
    try:
        collection = client.get_collection("rag_collection")
        print("Found existing collection. Deleting and recreating...")
        client.delete_collection("rag_collection")
    except:
        pass
    
    # Create a new collection
    collection = client.create_collection("rag_collection")
    
    # Process PDF one page at a time
    print(f"Processing PDF: {PDF_FILE_PATH}")
    
    # Try opening the PDF with more robust error handling
    try:
        # Try with more generous memory settings
        pdf_document = fitz.open(PDF_FILE_PATH, filetype="pdf")
        total_pages = pdf_document.page_count
        print(f"PDF has {total_pages} pages")
    except Exception as e:
        print(f"Error with standard opening: {e}")
        try:
            # Try alternative opening method
            stream = open(PDF_FILE_PATH, "rb").read()
            pdf_document = fitz.open("pdf", stream)
            total_pages = pdf_document.page_count
            print(f"PDF has {total_pages} pages (opened with alternative method)")
        except Exception as e:
            print(f"Failed to open PDF with alternative method: {e}")
            exit(1)
    
    total_chunks = 0
    
    # Process each page with a progress bar
    for page_num in tqdm(range(total_pages), desc="Processing PDF pages"):
        try:
            # Print start of page processing for debugging
            print(f"Starting to process page {page_num+1}...")
            
            # Process page and get text
            page_text = process_page(page_num, pdf_document)
            
            # Free up memory
            gc.collect()
            
            # Create chunks and store immediately
            chunks_created = create_and_store_chunks(page_text, page_num, client, collection)
            total_chunks += chunks_created
            
            print(f"Page {page_num+1}: Created and stored {chunks_created} chunks")
            
            # Free memory again
            del page_text
            gc.collect()
            
        except MemoryError:
            print(f"Memory error on page {page_num+1}, skipping...")
            continue
        except Exception as e:
            print(f"Error processing page {page_num+1}: {str(e)}")
            traceback.print_exc()
            continue
    
    pdf_document.close()
    
    print(f"RAG database built successfully with {total_chunks} total chunks!")
    print(f"Final collection count: {collection.count()} documents")H
