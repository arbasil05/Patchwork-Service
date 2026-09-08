import httpx
import urllib.parse
from typing import Dict, Any
from packaging.version import parse as parse_version, InvalidVersion

class PypiLookupError(Exception):
    """Raised when the PyPI registry API fails (network error, 5xx, timeout)."""
    pass

async def search_pypi_package(name: str) -> Dict[str, Any]:
    """
    Looks up a package in the PyPI registry.
    Returns a dict with exists, latest_version, and a capped list of versions.
    """
    encoded_name = urllib.parse.quote(name, safe='')
    url = f"https://pypi.org/pypi/{encoded_name}/json"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
    except httpx.RequestError as e:
        raise PypiLookupError(f"Failed to connect to PyPI registry: {str(e)}")
    
    if response.status_code == 404:
        return {
            "name": name,
            "exists": False,
            "latest_version": None,
            "versions": []
        }
        
    if response.status_code >= 500:
        raise PypiLookupError(f"PyPI registry returned an error: {response.status_code}")
        
    response.raise_for_status()
    data = response.json()
    
    info = data.get("info", {})
    latest_version = info.get("version")
    canonical_name = info.get("name", name)
    
    releases = data.get("releases", {})
    
    valid_versions = []
    prerelease_versions = []
    
    for version_str, release_list in releases.items():
        try:
            parsed = parse_version(version_str)
        except InvalidVersion:
            continue
            
        # Check if the version is yanked. A version is yanked if all its releases are yanked.
        # If there are no releases, we ignore it.
        if not release_list:
            continue
            
        is_yanked = all(r.get("yanked", False) for r in release_list)
        if is_yanked:
            continue
            
        if parsed.is_prerelease or parsed.is_devrelease or parsed.is_postrelease:
            # You said "non-prerelease unless that's all that exists", but to keep it simple,
            # we can store prereleases separately and only use them if no valid non-prereleases exist.
            if parsed.is_prerelease or parsed.is_devrelease:
                prerelease_versions.append((parsed, version_str))
            else:
                # Post-releases are generally considered stable
                valid_versions.append((parsed, version_str))
        else:
            valid_versions.append((parsed, version_str))
            
    # Sort descending
    valid_versions.sort(key=lambda x: x[0], reverse=True)
    
    # Fallback to prereleases if no stable versions exist
    if not valid_versions and prerelease_versions:
        prerelease_versions.sort(key=lambda x: x[0], reverse=True)
        valid_versions = prerelease_versions
        
    capped_versions = [v for parsed, v in valid_versions][:20]
    
    return {
        "name": canonical_name,
        "exists": True,
        "latest_version": latest_version,
        "versions": capped_versions
    }
