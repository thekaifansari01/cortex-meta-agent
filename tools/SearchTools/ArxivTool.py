import arxiv
import logging

def search_arxiv(query, max_results=3):
    """
    Fetches scientific and research papers from ArXiv using the latest Client-based API.
    """
    if not query: 
        return "Error: Empty ArXiv search query."
    
    try:
        client = arxiv.Client()
        
        query_lower = str(query).lower()
        recency_markers = ("latest", "recent", "newest", "2024", "2025", "this year", "current")
        if any(m in query_lower for m in recency_markers):
            _sort = arxiv.SortCriterion.SubmittedDate
        else:
            _sort = arxiv.SortCriterion.Relevance
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=_sort
        )
        
        results_text = f"🔬 ARXIV RESEARCH PAPERS FOR '{query}':\n\n"
        papers_found = False
        
        for result in client.results(search):
            papers_found = True
            
            clean_summary = result.summary.replace('\n', ' ').strip()
            _author_list = [a.name for a in result.authors]
            if len(_author_list) > 10:
                authors = ', '.join(_author_list[:10]) + f", +{len(_author_list) - 10} more"
            else:
                authors = ', '.join(_author_list)
            
            results_text += f"Title: {result.title}\n"
            try:
                published_str = result.published.strftime('%Y-%m-%d') if result.published else "Unknown"
            except Exception:
                published_str = "Unknown"
            results_text += f"Published: {published_str} | Category: {result.primary_category}\n"
            results_text += f"Authors: {authors}\n"
            results_text += f"Paper Link: {result.entry_id}\n"
            results_text += f"PDF Link: {result.pdf_url}\n"
            results_text += f"Abstract: {clean_summary}\n"
            results_text += "-" * 60 + "\n\n"
            
        return results_text if papers_found else f"No relevant research papers found on ArXiv."
        
    except Exception as e:
        logging.error(f"ArXiv Error: {e}")
        return f"ArXiv search failed: {str(e)}"

if __name__ == "__main__":
    print(search_arxiv("quantum error correction 2024", max_results=2))