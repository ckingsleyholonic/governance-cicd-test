# Try Holonic on an FTGO pull request

Open a draft pull request against `holonic-cicd-demo` to see how a code change can affect another service. The example below changes the Order service’s cancellation saga and lets you inspect the possible effect on a Kitchen ticket. You can do the whole exercise in GitHub with write access to this demo repository. The analysis pages are public and require no Holonic or AWS login.

## 1. Make a change on a new branch

Open [CancelOrderSaga.java on the demo branch](https://github.com/scottswindell-hub/governance-cicd-test/blob/holonic-cicd-demo/ftgo-order-service/src/main/java/net/chrisrichardson/ftgo/orderservice/sagas/cancelorder/CancelOrderSaga.java) and click the pencil to edit it. In `initializeSagaDefinition()`, find these two lines:

```java
.invokeParticipant(this::beginCancelTicket)
.withCompensation(this::undoBeginCancelTicket)
```

Keep the first line and delete the second. This leaves the forward cancellation step in place and removes its rollback action. Click **Commit changes**, choose **Create a new branch**, and give it a name such as `demo/alex-cancel-rollback`. Use your own name so testers can work independently.

## 2. Open a draft PR against the demo branch

On the **Open a pull request** screen, set **base** to `holonic-cicd-demo` and **compare** to your new branch. Check that **Files changed** shows your intended edit. Give the PR a title such as **Test cancellation without kitchen rollback**. The description is optional. Use the dropdown beside **Create pull request** and select **Create draft pull request**.

The base branch matters: the demo workflow runs on PRs targeting `holonic-cicd-demo`. [Compare branches in GitHub](https://github.com/scottswindell-hub/governance-cicd-test/compare/holonic-cicd-demo...holonic-cicd-demo)

## 3. Find the analysis on your PR

Open the PR’s **Checks** tab and look for **Holonic shared governance check**. If it says **Queued**, the shared runner is processing another PR. Once the run finishes, return to **Conversation** and find the **Holonic PR analysis** comment from `github-actions[bot]`.

| Comment row | What to do |
| --- | --- |
| ❌ Finding | Click the finding title. Each finding opens its own public page. |
| ✅ Completed | Read the row for the result. **No changes in scope** means there was nothing applicable to evaluate for that assessment. |
| ⚪ Inconclusive, partial coverage, or unavailable | Read the label for that assessment. A gray row leaves an open question about coverage. |

For the cancellation example, look under **Workflow specifications**. On the finding page, read **Behavior change** for the before, after, and potential downstream effect. Follow the affected specification into the expandable tree, then open **Impact graph** and **Service ecosystem** to explore the relationship between Order and Kitchen. A red workflow check is an expected outcome when the change violates an approved requirement. [See the bot comment on example PR #4](https://github.com/scottswindell-hub/governance-cicd-test/pull/4#issuecomment-6994758050)

To read the PR-wide analysis, including when there are no finding links, open `https://49s1mkl7jg.execute-api.us-east-1.amazonaws.com/reports/ftgo/pr/NUMBER` and replace `NUMBER` with your PR number.

## 4. Try a revision and compare

Push another commit to the same branch to trigger a fresh analysis. To try a behavior-preserving control, restore the compensation line and leave a whitespace-only edit so the PR still has a diff. The bot updates its existing comment after the run. Compare your result with [the preserving example on PR #5](https://github.com/scottswindell-hub/governance-cicd-test/pull/5#issuecomment-6994731479). Close your draft PR when you finish testing.
