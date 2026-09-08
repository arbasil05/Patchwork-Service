import httpx
import urllib.parse
from typing import Dict, Any
from packaging.version import parse as parse_version, InvalidVersion

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
    
    canonical_name = data.get("name", name)
    
    # Extract latest version
    dist_tags = data.get("dist-tags", {})
    latest_version = dist_tags.get("latest")
    

    versions_dict = data.get("versions", {})
    valid_versions = []
    for v in versions_dict.keys():
        try:
            parsed = parse_version(v)
            valid_versions.append((parsed, v))
        except InvalidVersion:
            pass

    # Sort versions descending by their parsed semver representation
    valid_versions.sort(key=lambda x: x[0], reverse=True)
    
    # Extract the original string versions, capped to 20
    capped_versions = [v for parsed, v in valid_versions][:20]
    
    return {
        "name": canonical_name,
        "exists": True,
        "latest_version": latest_version,
        "versions": capped_versions
    }
