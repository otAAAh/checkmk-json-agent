<!-- Paste this as a REPLY in the existing forum.checkmk.com topic (the one the
     0.12.0 announcement started), not as a new topic — hence no H1. One file per
     release, so a past announcement is never silently overwritten and the next
     one can be diffed against it. -->

**0.23.0** is out. It is a small release with no new options: the rule editor has
room for long URLs and paths, the Explorer no longer fails to load on recent 3.0
daily builds, and two things in the wizard are fixed. Most of the work happened
behind the scenes and changes nothing you can see. Nothing changes for an
existing rule.

## 📐 Room for the whole URL

At the default width, the rule editor cut off a URL after a few dozen characters,
and most JSON paths too. You had to click into the field and scroll to check what
was actually configured. Thanks to **martinhv** for asking.

The fields that hold long values are now wide:

- the endpoint URL and the OAuth 2.0 token URL;
- the request body, header values, the summary text and the value transform;
- the CA bundle, client certificate and key paths;
- every JSON path: fields, labels, pagination, the element filter, the
  transform's second field, the name suffix and the host field.

Short inputs such as names, keys and IDs keep their size. The wizard builds its
connection form from the same rule, so its fields are wider too.

## 🧭 Explorer: fixed for 3.0 daily builds

Recent **3.0 daily builds** moved one of Checkmk's icon types to a new module.
The Explorer imported it from the old place, so on those builds the whole package
failed to load: no *Generic JSON API* entry under **Setup → Quick setup**, and no
wizard. It now looks in the new place first and falls back to the old one, so
**2.5** loads exactly as before.

## 🔧 Two wizard fixes

- **The *+ Add* button is always within reach.** Every row of the field picker is
  as wide as its longest value. With one long string in the sample, the button
  ended up past the right edge of the tree on every row, and you had to scroll
  sideways to reach it. It now stays at the visible right edge, and long values
  scroll underneath it.
- **Label keys are previewed as the site will set them.** A label without a key
  of its own is named after the last key in its path. For keys with a dash or a
  space, the label summary and the review step showed the wrong name:
  `meta.content-type` appeared as `json_api/type`, while the site sets
  `json_api/content-type`. A path ending in an index (`items[0]`) showed
  `json_api/0` instead of `json_api/items`. Only the preview was wrong, never the
  rule or the labels on the site.

## 🧱 Under the hood

The special agent was a single 2,900-line script. It is now a small entry point
over seven modules: path grammar, transport, cache, OAuth 2.0, pagination,
fetching and extraction. The plugin's imports no longer assume where the package
is installed. **Nothing about the agent's behaviour changes.** Its command line
is the same, and its output was compared against 0.22.0's on 2.4 and on 3.0, with
identical results.

The two packages can still be updated **independently, in either order**. An
older Explorer works with this agent, and this Explorer works with an older agent.

## Upgrading

**No configuration change**, and nothing to check.

## Get it

- **Download:** [Releases](https://github.com/otAAAh/checkmk-json-agent/releases/tag/v0.23.0)
  — `json_api-0.23.0.mkp`, with SHA256 sums and build provenance attestation
- **Source, issues, ideas:** [github.com/otAAAh/checkmk-json-agent](https://github.com/otAAAh/checkmk-json-agent)
- Checkmk **2.4+**, any edition, verified on **3.0**

```
mkp add json_api-0.23.0.mkp
mkp enable json_api 0.23.0
```

On **Checkmk 2.5+**, update the optional companion package **Generic JSON API –
Explorer (extra)** (`json_api_explorer`) as well, to get the wizard fixes. On a
3.0 daily build, older Explorer versions fail to load.
