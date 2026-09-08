import httpx
import urllib.parse
from typing import Dict, Any

class NpmLookupError(Exception):
    """Raised when the NPM registry API fails (network error, 5xx, timeout)."""
    pass

async def search_npm_package(name: str) -> Dict[str, Any]:
    """
    Looks up a package in the NPM registry.
    Returns a dict with exists, latest_version, and a capped list of versions.
    """
    encoded_name = urllib.parse.quote(name, safe='@')
    url = f"https://registry.npmjs.org/{encoded_name}"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
    except httpx.RequestError as e:
        raise NpmLookupError(f"Failed to connect to NPM registry: {str(e)}")
    
    if response.status_code == 404:
        return {
            "name": name,
            "exists": False,
            "latest_version": None,
            "versions": []
        }
        
    if response.status_code >= 500:
        raise NpmLookupError(f"NPM registry returned an error: {response.status_code}")
        
    response.raise_for_status()
    data = response.json()
    
    # Extract latest version
    dist_tags = data.get("dist-tags", {})
    latest_version = dist_tags.get("latest")
    
    # Extract all versions, sort them conceptually (npm registry keys are usually chronological
    # in the "time" dict, but we can just use the keys from "versions" reversed, 
    # or rely on the "time" dict to sort them).
    # The "time" dict has keys for versions and their publish dates.
    times = data.get("time", {})
    # Filter out "modified" and "created" from times
    version_times = {k: v for k, v in times.items() if k not in ("modified", "created")}
    
    if version_times:
        # Sort versions by publish time descending
        sorted_versions = sorted(version_times.keys(), key=lambda k: version_times[k], reverse=True)
    else:
        # Fallback if "time" is missing: just reverse the keys of "versions"
        versions_dict = data.get("versions", {})
        sorted_versions = list(reversed(list(versions_dict.keys())))
        
    # Cap to 20 versions
    capped_versions = sorted_versions[:20]
    
    return {
        "name": name,
        "exists": True,
        "latest_version": latest_version,
        "versions": capped_versions
    }
